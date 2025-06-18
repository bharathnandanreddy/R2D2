# A global in-memory store for extracted requirements
from app.services.process_unstructured_requirements import load_requirements
requirement_list = []
requirement_loading = False
def get_requirements():
    global requirement_list
    global requirement_loading
    if(requirement_list == [] and not requirement_loading):
        requirement_loading = True
        requirement_list = load_requirements()
        requirement_loading = False
    return requirement_list

def add_requirement(req):
    requirement_list.append(req)

def remove_requirement(req_id):
    global requirement_list
    requirement_list = [r for r in requirement_list if r["requirement_id"] != req_id]

def clear_requirements():
    global requirement_list
    global requirement_loading
    requirement_list = []
    requirement_loading = False


def set_requirements(new_list):
    global requirement_list
    requirement_list = new_list

def update_requirement(req_id, updated_req):
    global requirement_list
    requirement_list = [updated_req if r["requirement_id"] == req_id else r for r in requirement_list]
