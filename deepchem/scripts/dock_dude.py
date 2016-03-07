"""
Pseudocode/TODOs

(0) create new directory with same subdirectories 

(1) prepare receptors:
  for each subdir in dud-e, find the receptor pdb, and prepare it. save it in new director

(2) prepare ligands:
  for each ligand: 
      if ligand does not already exist in new dir: prepare ligand 

      might have to use pybel to split up the mol2.gz
      cf: https://sourceforge.net/p/openbabel/mailman/message/27353258/
in between: make a pandas data frame with all ligand-protein combinations.

(3) dock:
  for each receptor, for each ligand (parallelize with iPython over rows in DF):
      find associated receptor and ligand in new directory
      do docking
      save in a new "docking" directory within receptor
      skip docking if that new docked pose already exists. 

"""

import os
import subprocess
from deepchem.featurizers.nnscore_utils import hydrogenate_and_compute_partial_charges
import pybel
import glob
import numpy as np
import time
from functools import partial
from ipyparallel import Client

def prepare_receptors(dude_dir, new_dir):
  for subdir, dirs, files in os.walk(dude_dir):
    receptor_name = os.path.basename(subdir)
    print("Currently examining receptor %s " % receptor_name)
    save_dir = os.path.join(new_dir, receptor_name)
    if not os.path.exists(save_dir):
      os.makedirs(save_dir)
    
    receptor_filename = os.path.join(subdir, "receptor.pdb")
    if not os.path.exists(receptor_filename):
      continue
    prepared_filename = os.path.join(save_dir, "%s.pdb" % receptor_name)
    prepared_pdbqt = os.path.join(save_dir, "%s.pdbqt" % receptor_name)

    hydrogenate_and_compute_partial_charges(receptor_filename, ".pdb",
                                            hyd_output=prepared_filename,
                                            pdbqt_output=prepared_pdbqt,
                                            verbose=False)

def prepare_ligands(mol2_file, save_dir):
  print("mol2_file")
  print(mol2_file)
  for i, mol in enumerate(pybel.readfile("mol2", mol2_file)):
    mol_name = mol.OBMol.GetTitle()
    #print("Preparing ligand %s" % mol_name)
    filename = mol_name + ".mol2"
    filename = os.path.join(save_dir, filename)
    prepared_filename = os.path.join(save_dir, "%s_prepared.pdb" %mol_name)
    prepared_pdbqt = os.path.join(save_dir, "%s_prepared.pdbqt" %mol_name)
    if os.path.exists(prepared_pdbqt):
      continue    
    output = open(filename,"w") # erase existing file, if any
    output.write(mol.write("mol2"))
    output.close()


    hydrogenate_and_compute_partial_charges(filename, "mol2",
                                            hyd_output=prepared_filename,
                                            pdbqt_output=prepared_pdbqt,
                                            verbose=False, protein=False)


def prepare_ligands_in_directory(dude_dir, new_dir, receptor_name=None):
  subdirs = glob.glob(os.path.join(dude_dir, '*/'))
  for subdir in subdirs:
    subdir = subdir.rstrip('/')
    if receptor_name is not None and receptor_name not in subdir:
      continue
    receptor_name = os.path.basename(subdir)
    print("Currently examining receptor %s " % receptor_name)
    save_dir = os.path.join(new_dir, receptor_name)
    input_mol2gz = os.path.join(subdir, "actives_final.mol2.gz")
    output_mol2 = os.path.join(subdir, "actives_final.mol2")
    try:
      subprocess.call("gunzip < %s > %s" %(input_mol2gz, output_mol2), shell=True)
    except:
      pass

    print("output_mol2")
    print(output_mol2)

    if not os.path.exists(output_mol2):
      continue

    prepare_ligands(output_mol2, save_dir)

    input_mol2gz = os.path.join(subdir, "decoys_final.mol2.gz")
    output_mol2 = os.path.join(subdir, "decoys_final.mol2")
    try:
      subprocess.call("gunzip < %s > %s" %(input_mol2gz, output_mol2), shell=True)
    except:
      pass

    prepare_ligands(output_mol2, save_dir)

def write_conf(receptor_filename, 
               ligand_filename,
               centroid,
               box_dims,
               conf_filename,
               exhaustiveness=1):
  
  with open(conf_filename, "wb") as f:
    f.write("receptor = %s\n" % receptor_filename)
    f.write("ligand = %s\n\n" % ligand_filename)

    f.write("center_x = %f\n" % centroid[0])
    f.write("center_y = %f\n" % centroid[1])
    f.write("center_z = %f\n\n" % centroid[2])

    f.write("size_x = %f\n" % box_dims[0])
    f.write("size_y = %f\n" % box_dims[1])
    f.write("size_z = %f\n\n" % box_dims[2])

    f.write("exhaustiveness = %d" % exhaustiveness)

  return

def dock_ligand_to_receptor(ligand_file, receptor_filename, protein_centroid,
                            box_dims, subdir, exhaustiveness):
  head, tail = os.path.split(ligand_file)
  ligand_name = os.path.splitext(tail)[0]
  print("Docking ligand %s to receptor %s" %(ligand_name, receptor_filename))
  conf_filename = os.path.join(subdir, "%s_conf.txt" % ligand_name)
  write_conf(receptor_filename, ligand_file, protein_centroid,
             box_dims, conf_filename, exhaustiveness=1)

  log_filename = os.path.join(subdir, "%s_log.txt" % ligand_name)
  out_filename = os.path.join(subdir, "%s_docked.pdbqt" % ligand_name)
  subprocess.call("/scratch/users/enf/software/autodock_vina_1_1_2_linux_x86/bin/vina --config %s --log %s --out %s" % (conf_filename, log_filename, out_filename), shell=True)
  return out_filename

def get_molecule_data(pybel_molecule):
  atom_positions = []
  for atom in pybel_molecule:
    atom_positions.append(atom.coords)
  num_atoms = len(atom_positions)
  protein_xyz = np.asarray(atom_positions)
  protein_centroid = np.mean(protein_xyz, axis=0)
  protein_max = np.max(protein_xyz, axis=0)
  protein_min = np.min(protein_xyz, axis=0)
  protein_range = protein_max - protein_min
  return protein_centroid, protein_range

def dock_ligands_to_receptors(docking_dir, worker_pool=False, exhaustiveness=1, chosen_receptor=None, restrict_box=True):
  subdirs = glob.glob(os.path.join(docking_dir, '*/'))
  for subdir in subdirs:
    subdir = subdir.rstrip('/')
    receptor_name = os.path.basename(subdir)
    if chosen_receptor is not None:
      if chosen_receptor != receptor_name:
        continue
    print("receptor name = %s" % receptor_name)
    receptor_filename = os.path.join(subdir, "%s.pdbqt" % receptor_name)
    if not os.path.exists(receptor_filename):
      continue

    print("Examining %s" % receptor_filename)

    receptor_pybel = pybel.readfile("pdb", 
        os.path.join(subdir, "%s.pdb" % receptor_name)).next()
    protein_centroid, protein_range = get_molecule_data(receptor_pybel)

    box_dims = protein_range + 5.0

    ligands = sorted(glob.glob(os.path.join(subdir, '*_prepared.pdbqt')))
    print("Num ligands = %d" % len(ligands))


    dock_ligand_to_receptor_partial = partial(dock_ligand_to_receptor, receptor_filename=receptor_filename,
                                              protein_centroid=protein_centroid, box_dims=box_dims,
                                              subdir=subdir, exhaustiveness=exhaustiveness)

    if restrict_box:
      active_ligand = ""
      for ligand in ligands:
        if "CHEM" in ligand:
          active_ligand = ligand
          break

      print("Docking to %s first to ascertain centroid and box dimensions" % active_ligand)

      out_pdb_qt = dock_ligand_to_receptor_partial(active_ligand)
      ligand_pybel = pybel.readfile("pdbqt", 
                                    out_pdb_qt).next()
      ligand_centroid, _ = get_molecule_data(ligand_pybel)
      print("Protein centroid = %s" %(str(protein_centroid)))
      print("Ligand centroid = %s" %(str(ligand_centroid)))
      box_dims = np.array([20., 20., 20.])
      dock_ligand_to_receptor_partial = partial(dock_ligand_to_receptor, receptor_filename=receptor_filename,
                                          protein_centroid=ligand_centroid, box_dims=box_dims,
                                          subdir=subdir, exhaustiveness=exhaustiveness)

      print("Finished docking to %s, docking to remainder of ligands now." % active_ligand)

    if worker_pool is False:
      for i, ligand_file in enumerate(ligands):
        a = time.time()
        dock_ligand_to_receptor_partial(ligand)
        print("took %f seconds to dock single ligand." %(time.time() - a))
    else:
      c = Client()
      dview = c[:]
      print("parallelizing docking over worker pool")

      dview.map_sync(dock_ligand_to_receptor_partial, ligands)

def prepare_ligands_and_dock_ligands_to_receptors(dude_dir, docking_dir, worker_pool):
  subdirs = sorted(glob.glob(os.path.join(docking_dir, '*/')))
  for subdir in subdirs:
    subdir = subdir.rstrip('/')
    receptor_name = os.path.basename(subdir)
    print("Preparing ligands and then docking to %s" % receptor_name)
    prepare_ligands_in_directory(dude_dir, docking_dir, receptor_name)
    time.sleep(10)
    dock_ligands_to_receptors(docking_dir, worker_pool, chosen_receptor=receptor_name)
    break

def prepare_receptors_prepare_ligands_dock_ligands_to_receptors(dude_dir, docking_dir, worker_pool):
  prepare_receptors(dude_dir, docking_dir)
  prepare_ligands_and_dock_ligands_to_receptors(dude_dir, docking_dir, worker_pool)
