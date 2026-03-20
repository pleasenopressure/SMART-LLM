import copy
import ast
import glob
import json
import os
import argparse
from pathlib import Path
from datetime import datetime
import random

import sys
sys.path.append(".")

try:
    import ai2thor.controller as ai2thor_controller
except Exception:
    ai2thor_controller = None

try:
    from llm_client import LLMClient
except ModuleNotFoundError:
    from scripts.llm_client import LLMClient

import resources.actions as actions
import resources.robots as robots


LLM_CLIENT = None


def LM(prompt, max_tokens=128, temperature=0, stop=None, logprobs=1, frequency_penalty=0):
    return LLM_CLIENT.generate(
        prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        stop=stop,
        logprobs=logprobs,
        frequency_penalty=frequency_penalty,
    )

# Function returns object list with name and properties.
def convert_to_dict_objprop(objs, obj_mass):
    objs_dict = []
    for i, obj in enumerate(objs):
        obj_dict = {'name': obj , 'mass' : obj_mass[i]}
        # obj_dict = {'name': obj , 'mass' : 1.0}
        objs_dict.append(obj_dict)
    return objs_dict

def load_fallback_objects():
    fallback_path = Path(os.getcwd()) / "data" / "pythonic_plans" / "train_task_allocation_solution.py"
    with open(fallback_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith("objects = "):
                return ast.literal_eval(stripped[len("objects = "):])

    raise RuntimeError("Could not load the fallback AI2Thor object catalog.")

def get_ai2_thor_objects(floor_plan_id):
    fallback_objects = load_fallback_objects()

    if ai2thor_controller is None:
        print("AI2Thor is unavailable in this environment; using a static object catalog for prompt generation.")
        return fallback_objects

    try:
        # connector to ai2thor to get object list
        controller = ai2thor_controller.Controller(scene="FloorPlan" + str(floor_plan_id))
        obj = list([obj["objectType"] for obj in controller.last_event.metadata["objects"]])
        obj_mass = list([obj["mass"] for obj in controller.last_event.metadata["objects"]])
        controller.stop()
        obj = convert_to_dict_objprop(obj, obj_mass)
        return obj
    except Exception as exc:
        print(f"AI2Thor scene inspection failed ({exc}); using a static object catalog for prompt generation.")
        return fallback_objects

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--floor-plan", type=int, required=True)
    parser.add_argument("--openai-api-key-file", "--api-key-file", dest="api_key_file", type=str, default="api_key")
    parser.add_argument("--provider", type=str, default="auto", choices=["auto", "openai", "deepseek", "gemini"])
    parser.add_argument("--gpt-version", type=str, default="gpt-4", 
                        help="LLM model name, for example gpt-4o-mini, deepseek-chat, or gemini-2.0-flash")
    parser.add_argument("--base-url", type=str, default=None)
    
    parser.add_argument("--prompt-decompse-set", type=str, default="train_task_decompose", 
                        choices=['train_task_decompose'])
    
    parser.add_argument("--prompt-allocation-set", type=str, default="train_task_allocation", 
                        choices=['train_task_allocation'])
    
    parser.add_argument("--test-set", type=str, default="final_test", 
                        choices=['final_test'])
    
    parser.add_argument("--log-results", type=bool, default=True)
    
    args = parser.parse_args()

    LLM_CLIENT = LLMClient.from_config(
        provider=args.provider,
        model=args.gpt_version,
        api_key_file=args.api_key_file,
        base_url=args.base_url,
    )
    
    if not os.path.isdir(f"./logs/"):
        os.makedirs(f"./logs/")
        
    # read the tasks        
    test_tasks = []
    robots_test_tasks = []  
    gt_test_tasks = []    
    trans_cnt_tasks = []
    max_trans_cnt_tasks = []  
    with open (f"./data/{args.test_set}/FloorPlan{args.floor_plan}.json", "r") as f:
        for line in f.readlines():
            test_tasks.append(list(json.loads(line).values())[0])
            robots_test_tasks.append(list(json.loads(line).values())[1])
            gt_test_tasks.append(list(json.loads(line).values())[2])
            trans_cnt_tasks.append(list(json.loads(line).values())[3])
            max_trans_cnt_tasks.append(list(json.loads(line).values())[4])
                    
    print(f"\n----Test set tasks----\n{test_tasks}\nTotal: {len(test_tasks)} tasks\n")
    # prepare list of robots for the tasks
    available_robots = []
    for robots_list in robots_test_tasks:
        task_robots = []
        for i, r_id in enumerate(robots_list):
            rob = copy.deepcopy(robots.robots[r_id - 1])
            # rename the robot
            rob['name'] = 'robot' + str(i+1)
            task_robots.append(rob)
        available_robots.append(task_robots)
        
    
    ######## Train Task Decomposition ########
        
    # prepare train decompostion demonstration for ai2thor samples
    prompt = f"from skills import " + actions.ai2thor_actions
    prompt += f"\nimport time"
    prompt += f"\nimport threading"
    objects_ai = f"\n\nobjects = {get_ai2_thor_objects(args.floor_plan)}"
    prompt += objects_ai
    
    # read input train prompts
    decompose_prompt_file = open(os.getcwd() + "/data/pythonic_plans/" + args.prompt_decompse_set + ".py", "r")
    decompose_prompt = decompose_prompt_file.read()
    decompose_prompt_file.close()
    
    prompt += "\n\n" + decompose_prompt
    
    print ("Generating Decompsed Plans...")
    
    decomposed_plan = []
    for task in test_tasks:
        curr_prompt =  f"{prompt}\n\n# Task Description: {task}"

        messages = [{"role": "user", "content": curr_prompt}]
        _, text = LM(messages, max_tokens=1300, frequency_penalty=0.0)

        decomposed_plan.append(text)
        
    print ("Generating Allocation Solution...")

    ######## Train Task Allocation - SOLUTION ########
    prompt = f"from skills import " + actions.ai2thor_actions
    prompt += f"\nimport time"
    prompt += f"\nimport threading"
    
    prompt_file = os.getcwd() + "/data/pythonic_plans/" + args.prompt_allocation_set + "_solution.py"
    allocated_prompt_file = open(prompt_file, "r")
    allocated_prompt = allocated_prompt_file.read()
    allocated_prompt_file.close()
    
    prompt += "\n\n" + allocated_prompt + "\n\n"
    
    allocated_plan = []
    allocation_system_prompt = (
        "You are a Robot Task Allocation Expert. Determine whether the subtasks must be "
        "performed sequentially or in parallel, or a combination of both based on your "
        "reasoning. In the case of Task Allocation based on Robot Skills alone - First "
        "check if robot teams are required. Then ensure that robot skills or robot team "
        "skills match the required skills for the subtask when allocating. In the case of "
        "Task Allocation based on Mass alone - First check if robot teams are required. "
        "Then ensure that robot mass capacity or robot team combined mass capacity is "
        "greater than or equal to the mass for the object when allocating. In both cases, "
        "if there are multiple options for allocation, pick the best available option."
    )
    for i, plan in enumerate(decomposed_plan):
        no_robot  = len(available_robots[i])
        curr_prompt = prompt + plan
        curr_prompt += f"\n# TASK ALLOCATION"
        curr_prompt += f"\n# Scenario: There are {no_robot} robots available, The task should be performed using the minimum number of robots necessary. Robots should be assigned to subtasks that match its skills and mass capacity. Using your reasoning come up with a solution to satisfy all contraints."
        curr_prompt += f"\n\nrobots = {available_robots[i]}"
        curr_prompt += f"\n{objects_ai}"
        curr_prompt += f"\n\n# IMPORTANT: The AI should ensure that the robots assigned to the tasks have all the necessary skills to perform the tasks. IMPORTANT: Determine whether the subtasks must be performed sequentially or in parallel, or a combination of both and allocate robots based on availablitiy. "
        curr_prompt += f"\n# SOLUTION  \n"

        messages = [
            {"role": "system", "content": allocation_system_prompt},
            {"role": "user", "content": curr_prompt},
        ]
        _, text = LM(messages, max_tokens=700, frequency_penalty=0.35)

        allocated_plan.append(text)
    
    print ("Generating Allocated Code...")
    
    ######## Train Task Allocation - CODE Solution ########

    prompt = f"from skills import " + actions.ai2thor_actions
    prompt += f"\nimport time"
    prompt += f"\nimport threading"
    prompt += objects_ai
    
    code_plan = []
    code_system_prompt = "You are a Robot Task Allocation Expert. Return only valid Python code."

    prompt_file1 = os.getcwd() + "/data/pythonic_plans/" + args.prompt_allocation_set + "_code.py"
    code_prompt_file = open(prompt_file1, "r")
    code_prompt = code_prompt_file.read()
    code_prompt_file.close()
    
    prompt += "\n\n" + code_prompt + "\n\n"

    for i, (plan, solution) in enumerate(zip(decomposed_plan,allocated_plan)):
        curr_prompt = prompt + plan
        curr_prompt += f"\n# TASK ALLOCATION"
        curr_prompt += f"\n\nrobots = {available_robots[i]}"
        curr_prompt += solution
        curr_prompt += f"\n# CODE Solution  \n"

        messages = [
            {"role": "system", "content": code_system_prompt},
            {"role": "user", "content": curr_prompt},
        ]
        _, text = LM(messages, max_tokens=1400, frequency_penalty=0.4)

        code_plan.append(text)
    
    # save generated plan
    exec_folders = []
    if args.log_results:
        line = {}
        now = datetime.now() # current date and time
        date_time = now.strftime("%m-%d-%Y-%H-%M-%S")
        
        for idx, task in enumerate(test_tasks):
            task_name = "{fxn}".format(fxn = '_'.join(task.split(' ')))
            task_name = task_name.replace('\n','')
            folder_name = f"{task_name}_plans_{date_time}"
            exec_folders.append(folder_name)
            
            os.mkdir("./logs/"+folder_name)
     
            with open(f"./logs/{folder_name}/log.txt", 'w') as f:
                f.write(task)
                f.write(f"\n\nLLM Provider: {LLM_CLIENT.provider}")
                f.write(f"\nLLM Model: {args.gpt_version}")
                f.write(f"\n\nFloor Plan: {args.floor_plan}")
                f.write(f"\n{objects_ai}")
                f.write(f"\nrobots = {available_robots[idx]}")
                f.write(f"\nground_truth = {gt_test_tasks[idx]}")
                f.write(f"\ntrans = {trans_cnt_tasks[idx]}")
                f.write(f"\nmax_trans = {max_trans_cnt_tasks[idx]}")

            with open(f"./logs/{folder_name}/decomposed_plan.py", 'w') as d:
                d.write(decomposed_plan[idx])
                
            with open(f"./logs/{folder_name}/allocated_plan.py", 'w') as a:
                a.write(allocated_plan[idx])
                
            with open(f"./logs/{folder_name}/code_plan.py", 'w') as x:
                x.write(code_plan[idx])
            
