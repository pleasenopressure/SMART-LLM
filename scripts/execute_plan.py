import os
from pathlib import Path
import subprocess
import argparse

def append_trans_ctr(allocated_plan):
    brk_ctr = 0
    code_segs = allocated_plan.split("\n\n")
    fn_calls = []
    for cd in code_segs:
        if "def" not in cd and "threading.Thread" not in cd and "join" not in cd and cd[-1] == ")":
            # fn_calls.append(cd)
            brk_ctr += 1
    print ("No Breaks: ", brk_ctr)
    return brk_ctr

def read_log_metadata(log_lines):
    metadata = {}

    for line in log_lines:
        stripped = line.strip()
        if stripped.startswith("Floor Plan:"):
            metadata["floor_plan"] = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("robots ="):
            metadata["robots"] = stripped.split("=", 1)[1].strip()
        elif stripped.startswith("ground_truth ="):
            metadata["ground_truth"] = stripped.split("=", 1)[1].strip()
        elif stripped.startswith("trans ="):
            metadata["trans"] = stripped.split("=", 1)[1].strip()
        elif stripped.startswith("max_trans ="):
            metadata["max_trans"] = stripped.split("=", 1)[1].strip()

    if "robots" not in metadata and len(log_lines) > 8:
        metadata["robots"] = log_lines[8].strip()
    if "floor_plan" not in metadata and len(log_lines) > 4:
        metadata["floor_plan"] = log_lines[4].split(":", 1)[-1].strip()
    if "ground_truth" not in metadata and len(log_lines) > 9:
        metadata["ground_truth"] = log_lines[9].strip()
    if "trans" not in metadata and len(log_lines) > 10:
        metadata["trans"] = log_lines[10].split("=", 1)[-1].strip()
    if "max_trans" not in metadata and len(log_lines) > 11:
        metadata["max_trans"] = log_lines[11].split("=", 1)[-1].strip()

    return metadata

def compile_aithor_exec_file(expt_name):
    log_path = os.getcwd() + "/logs/" + expt_name
    executable_plan = ""
    
    # append the imports to the file
    import_file = Path(os.getcwd() + "/data/aithor_connect/imports_aux_fn.py").read_text()
    executable_plan += (import_file + "\n")
    
    # append the list of robots and floor plan number
    with open(log_path + "/log.txt", "r", encoding="utf-8") as log_file:
        log_data = log_file.readlines()

    metadata = read_log_metadata(log_data)
    if "robots" in metadata:
        executable_plan += ("robots = " + metadata["robots"] + "\n")
    if "floor_plan" in metadata:
        executable_plan += ("floor_no = " + metadata["floor_plan"] + "\n\n")
    if "ground_truth" in metadata:
        executable_plan += ("ground_truth = " + metadata["ground_truth"] + "\n")
    if "trans" in metadata:
        executable_plan += ("no_trans_gt = " + metadata["trans"] + "\n")
    if "max_trans" in metadata:
        executable_plan += ("max_trans = " + metadata["max_trans"] + "\n")
    
    # append the ai thoe connector and helper fns
    connector_file = Path(os.getcwd() + "/data/aithor_connect/aithor_connect.py").read_text()
    executable_plan += (connector_file + "\n")
    
    # append the allocated plan
    allocated_plan = Path(log_path + "/code_plan.py").read_text()
    brks = append_trans_ctr(allocated_plan)
    executable_plan += (allocated_plan + "\n")
    executable_plan += ("no_trans = " + str(brks) + "\n")

    # append the task thread termination
    terminate_plan = Path(os.getcwd() + "/data/aithor_connect/end_thread.py").read_text()
    executable_plan += (terminate_plan + "\n")

    with open(f"{log_path}/executable_plan.py", 'w') as d:
        d.write(executable_plan)
        
    return (f"{log_path}/executable_plan.py")

parser = argparse.ArgumentParser()
parser.add_argument("--command", type=str, required=True)
args = parser.parse_args()

if os.name == "nt":
    raise RuntimeError(
        "AI2Thor execution is not supported on native Windows. "
        "Run this step inside Ubuntu, WSL2, or Docker with a Linux Python environment."
    )

expt_name = args.command
print (expt_name)
ai_exec_file = compile_aithor_exec_file(expt_name)

subprocess.run(["python", ai_exec_file])
