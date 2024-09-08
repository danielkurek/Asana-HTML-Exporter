import asana
from asana.rest import ApiException
from pprint import pprint
import os
from dotenv import load_dotenv
from typing import Self
import json
from pathlib import Path
from slugify import slugify
import requests
import humanize
from tqdm import tqdm
from jinja2 import Environment, FileSystemLoader, select_autoescape

load_dotenv()

class SavableHierEntity:
    base_path = "out/"
    def __init__(self, name: str, parent: Self, raw_data:dict = None):
        self.name = name
        self.parent = parent
        self.raw_data = raw_data
    
    def path(self) -> (Path, str):
        obj = self.parent
        obj_path = []
        while obj is not None and isinstance(obj, SavableHierEntity):
            obj_path.append(obj)
            obj = obj.parent
        obj_path.reverse()
        path = Path(self.base_path)
        for o in obj_path:
            path = path / slugify(str(o.name))
        return path, slugify(str(self.name))
            
    def save_raw(self):
        path, filename = self.path()
        if not path.exists():
            path.mkdir(parents=True)
        with open(path / (filename + ".json"), mode="w") as f:
            json.dump(self.raw_data, f, indent=2)

class Attachment(SavableHierEntity):
    def __init__(self, name: str, download_url: str, created_at: str, size: int, parent: 'Task' = None, raw_data: dict = None):
        self.download_url = download_url
        self.created_at = created_at
        self.size = size
        super().__init__(name, parent, raw_data)
    
    def from_data(data: dict, parent = None):
        return Attachment(data["name"], data["download_url"], data["created_at"], data["size"], parent=parent, raw_data=data)
    
    def path(self) -> (Path, str):
        path, filename = super().path()
        path = path / "attachments"
        return path, str(self.name)
    
    def save(self):
        if self.download_url is None:
            raise Exception("Download URL of an attachment is not specified")
        if self.name is None:
            raise Exception("Name of an attachment is not specified")
        path, filename = self.path()
        resp = requests.get(self.download_url, stream=True)
        size = int(resp.headers.get('content-length', 0))
        block_size = 1024
        size_str = ""
        if self.size is not None:
            size_str = f" ({humanize.naturalsize(self.size, binary=True)})"
        print(f"Downloading {filename}{size_str}")
        with tqdm(total=size, unit="B", unit_scale=True) as progress_bar:
            with open(path / filename, mode="wb") as f:
                for data in resp.iter_content(block_size):
                    progress_bar.update(len(data))
                    f.write(data)

# Story is a comment on task or an update message
class Story(SavableHierEntity):
    # TODO: how are represented attachments within comments
    def __init__(self, gid: str, story_type: str, likes: list, text: str, parent: Self = None, raw_data: dict = None):
        self.gid = gid
        self.story_type = story_type
        self.likes = likes
        self.text = text
        super().__init__("story_"+str(gid), parent, raw_data)
    
    @staticmethod
    def from_data(data: dict, parent = None):
        return Story(data["gid"], data["type"], data.get("likes"), data["html_text"], parent=parent, raw_data=data)
    
    def path(self) -> (Path, str):
        path, filename = super().path()
        path = path / "stories"
        return path, filename

class Task(SavableHierEntity):
    def __init__(self, gid: str, name: str, due_at: str, due_on: str, followers: list, notes: str, num_subtasks: int, tags: list, parent: Self | 'Project' = None, raw_data: dict = None):
        self.gid = gid
        self.due_at = due_at
        self.due_on = due_on
        self.followers = followers
        self.notes = notes
        self.num_subtasks = num_subtasks
        self.subtasks = None
        self.tags = tags
        self.stories = None
        self.attachments = None
        super().__init__(name, parent, raw_data)
    
    def __repr__(self):
        return f"Task(\n\t{self.gid=},\n\t{self.name=},\n\t{self.due_at=},\n\t{self.due_on=},\n\t{self.followers=},\n\t{self.notes=},\n\t{self.num_subtasks=},\n\t{self.subtasks=},\n\t{self.tags=},\n\t{len(self.stories)=}\n\t)"
    
    def from_data(data: dict, parent = None):
        return Task(data["gid"], data["name"], data["due_at"], data["due_on"], data["followers"], data["html_notes"], data["num_subtasks"], data["tags"], parent=parent, raw_data=data)
    
    def get_all(self):
        self.get_stories()
        self.get_attachments()
        subtasks = self.get_subtasks()
        for sub in subtasks:
            sub.get_all()

    def get_stories(self) -> list[Story]:
        if AsanaExporter.api_client is None:
            raise Exception("No asana api client defined")
        # create an instance of the API class
        stories_api_instance = asana.StoriesApi(AsanaExporter.api_client)
        opts = {
            'limit': 50, # int | Results per page. The number of objects to return per page. The value must be between 1 and 100.
            # 'offset': "eyJ0eXAiOJiKV1iQLCJhbGciOiJIUzI1NiJ9", # str | Offset token. An offset to the next page returned by the API. A pagination request will return an offset token, which can be used as an input parameter to the next request. If an offset is not passed in, the API will return the first page of results. *Note: You can only pass in an offset that was returned to you via a previously paginated request.*
            'opt_fields': "assignee,assignee.name,created_at,created_by,created_by.name,custom_field,custom_field.date_value,custom_field.date_value.date,custom_field.date_value.date_time,custom_field.display_value,custom_field.enabled,custom_field.enum_options,custom_field.enum_options.color,custom_field.enum_options.enabled,custom_field.enum_options.name,custom_field.enum_value,custom_field.enum_value.color,custom_field.enum_value.enabled,custom_field.enum_value.name,custom_field.id_prefix,custom_field.is_formula_field,custom_field.multi_enum_values,custom_field.multi_enum_values.color,custom_field.multi_enum_values.enabled,custom_field.multi_enum_values.name,custom_field.name,custom_field.number_value,custom_field.representation_type,custom_field.resource_subtype,custom_field.text_value,custom_field.type,dependency,dependency.created_by,dependency.name,dependency.resource_subtype,duplicate_of,duplicate_of.created_by,duplicate_of.name,duplicate_of.resource_subtype,duplicated_from,duplicated_from.created_by,duplicated_from.name,duplicated_from.resource_subtype,follower,follower.name,hearted,hearts,hearts.user,hearts.user.name,html_text,is_editable,is_edited,is_pinned,liked,likes,likes.user,likes.user.name,new_approval_status,new_date_value,new_dates,new_dates.due_at,new_dates.due_on,new_dates.start_on,new_enum_value,new_enum_value.color,new_enum_value.enabled,new_enum_value.name,new_multi_enum_values,new_multi_enum_values.color,new_multi_enum_values.enabled,new_multi_enum_values.name,new_name,new_number_value,new_people_value,new_people_value.name,new_resource_subtype,new_section,new_section.name,new_text_value,num_hearts,num_likes,offset,old_approval_status,old_date_value,old_dates,old_dates.due_at,old_dates.due_on,old_dates.start_on,old_enum_value,old_enum_value.color,old_enum_value.enabled,old_enum_value.name,old_multi_enum_values,old_multi_enum_values.color,old_multi_enum_values.enabled,old_multi_enum_values.name,old_name,old_number_value,old_people_value,old_people_value.name,old_resource_subtype,old_section,old_section.name,old_text_value,path,previews,previews.fallback,previews.footer,previews.header,previews.header_link,previews.html_text,previews.text,previews.title,previews.title_link,project,project.name,resource_subtype,source,sticker_name,story,story.created_at,story.created_by,story.created_by.name,story.resource_subtype,story.text,tag,tag.name,target,target.created_by,target.name,target.resource_subtype,task,task.created_by,task.name,task.resource_subtype,text,type,uri", # list[str] | This endpoint returns a compact resource, which excludes some properties by default. To include those optional properties, set this query parameter to a comma-separated list of the properties you wish to include.
        }
        stories = []
        try:
            # Get stories from a task
            api_response = stories_api_instance.get_stories_for_task(self.gid, opts)
            for data in api_response:
                stories.append(Story.from_data(data, parent=self))
        except ApiException as e:
            print("Exception when calling StoriesApi->get_stories_for_task: %s\n" % e)
        self.stories = stories
        return stories
    
    def get_attachments(self) -> list[Attachment]:
        if AsanaExporter.api_client is None:
            raise Exception("No asana api client defined")
        # create an instance of the API class
        attachments_api_instance = asana.AttachmentsApi(AsanaExporter.api_client)
        opts = {
            'limit': 50, # int | Results per page. The number of objects to return per page. The value must be between 1 and 100.
            # 'offset': "eyJ0eXAiOJiKV1iQLCJhbGciOiJIUzI1NiJ9", # str | Offset token. An offset to the next page returned by the API. A pagination request will return an offset token, which can be used as an input parameter to the next request. If an offset is not passed in, the API will return the first page of results. *Note: You can only pass in an offset that was returned to you via a previously paginated request.*
            'opt_fields': "connected_to_app,created_at,download_url,host,name,offset,parent,parent.created_by,parent.name,parent.resource_subtype,path,permanent_url,resource_subtype,size,uri,view_url", # list[str] | This endpoint returns a compact resource, which excludes some properties by default. To include those optional properties, set this query parameter to a comma-separated list of the properties you wish to include.
        }
        attachments = []
        try:
            # Get attachments from an object
            api_response = attachments_api_instance.get_attachments_for_object(self.gid, opts)
            for data in api_response:
                attachments.append(Attachment.from_data(data, parent=self))
        except ApiException as e:
            print("Exception when calling AttachmentsApi->get_attachments_for_object: %s\n" % e)
        self.attachments = attachments
        return self.attachments
    
    def get_subtasks(self) -> list[Self]:
        if AsanaExporter.api_client is None:
            raise Exception("No asana api client defined")
        # create an instance of the API class
        tasks_api_instance = asana.TasksApi(AsanaExporter.api_client)
        opts = {
            # 'completed_since': "2012-02-22T02:06:58.158Z", # str | Only return tasks that are either incomplete or that have been completed since this time. Accepts a date-time string or the keyword *now*. 
            'limit': 50, # int | Results per page. The number of objects to return per page. The value must be between 1 and 100.
            # 'offset': "eyJ0eXAiOJiKV1iQLCJhbGciOiJIUzI1NiJ9", # str | Offset token. An offset to the next page returned by the API. A pagination request will return an offset token, which can be used as an input parameter to the next request. If an offset is not passed in, the API will return the first page of results. *Note: You can only pass in an offset that was returned to you via a previously paginated request.*
            'opt_fields': "actual_time_minutes,approval_status,assignee,assignee.name,assignee_section,assignee_section.name,assignee_status,completed,completed_at,completed_by,completed_by.name,created_at,created_by,custom_fields,custom_fields.asana_created_field,custom_fields.created_by,custom_fields.created_by.name,custom_fields.currency_code,custom_fields.custom_label,custom_fields.custom_label_position,custom_fields.date_value,custom_fields.date_value.date,custom_fields.date_value.date_time,custom_fields.description,custom_fields.display_value,custom_fields.enabled,custom_fields.enum_options,custom_fields.enum_options.color,custom_fields.enum_options.enabled,custom_fields.enum_options.name,custom_fields.enum_value,custom_fields.enum_value.color,custom_fields.enum_value.enabled,custom_fields.enum_value.name,custom_fields.format,custom_fields.has_notifications_enabled,custom_fields.id_prefix,custom_fields.is_formula_field,custom_fields.is_global_to_workspace,custom_fields.is_value_read_only,custom_fields.multi_enum_values,custom_fields.multi_enum_values.color,custom_fields.multi_enum_values.enabled,custom_fields.multi_enum_values.name,custom_fields.name,custom_fields.number_value,custom_fields.people_value,custom_fields.people_value.name,custom_fields.precision,custom_fields.representation_type,custom_fields.resource_subtype,custom_fields.text_value,custom_fields.type,dependencies,dependents,due_at,due_on,external,external.data,followers,followers.name,hearted,hearts,hearts.user,hearts.user.name,html_notes,is_rendered_as_separator,liked,likes,likes.user,likes.user.name,memberships,memberships.project,memberships.project.name,memberships.section,memberships.section.name,modified_at,name,notes,num_hearts,num_likes,num_subtasks,offset,parent,parent.created_by,parent.name,parent.resource_subtype,path,permalink_url,projects,projects.name,resource_subtype,start_at,start_on,tags,tags.name,uri,workspace,workspace.name", # list[str] | This endpoint returns a compact resource, which excludes some properties by default. To include those optional properties, set this query parameter to a comma-separated list of the properties you wish to include.
        }
        subtasks = []
        try:
            # Get tasks from a project
            api_response = tasks_api_instance.get_tasks_for_project(self.gid, opts)
            for data in api_response:
                subtasks.append(Task.from_data(data, parent=self))
        except ApiException as e:
            print("Exception when calling TasksApi->get_tasks_for_project: %s\n" % e)
        self.subtasks = subtasks
        return self.subtasks

class Project(SavableHierEntity):
    def __init__(self, gid: str, name: str, color: str, modified_at: str, parent: 'Workspace' = None, raw_data: dict = None):
        self.gid = gid
        self.color = color
        self.modified_at = modified_at
        self.tasks = None
        super().__init__(name, parent, raw_data)
    
    def __repr__(self):
        return f"Project(\n\t{self.gid=},\n\t{self.name=},\n\t{self.color=},\n\t{self.modified_at=}\n\t)"
    
    @staticmethod
    def from_data(data: dict, parent = None):
        return Project(data["gid"], data["name"], data["color"], data["modified_at"], parent=parent, raw_data=data)
    
    def get_all(self):
        tasks = self.get_tasks()
        for tsk in tasks:
            tsk.get_all()
    
    def get_tasks(self):
        if AsanaExporter.api_client is None:
            raise Exception("No asana api client defined")
        # create an instance of the API class
        tasks_api_instance = asana.TasksApi(AsanaExporter.api_client)
        opts = {
            # 'completed_since': "2012-02-22T02:06:58.158Z", # str | Only return tasks that are either incomplete or that have been completed since this time. Accepts a date-time string or the keyword *now*. 
            'limit': 50, # int | Results per page. The number of objects to return per page. The value must be between 1 and 100.
            # 'offset': "eyJ0eXAiOJiKV1iQLCJhbGciOiJIUzI1NiJ9", # str | Offset token. An offset to the next page returned by the API. A pagination request will return an offset token, which can be used as an input parameter to the next request. If an offset is not passed in, the API will return the first page of results. *Note: You can only pass in an offset that was returned to you via a previously paginated request.*
            'opt_fields': "actual_time_minutes,approval_status,assignee,assignee.name,assignee_section,assignee_section.name,assignee_status,completed,completed_at,completed_by,completed_by.name,created_at,created_by,custom_fields,custom_fields.asana_created_field,custom_fields.created_by,custom_fields.created_by.name,custom_fields.currency_code,custom_fields.custom_label,custom_fields.custom_label_position,custom_fields.date_value,custom_fields.date_value.date,custom_fields.date_value.date_time,custom_fields.description,custom_fields.display_value,custom_fields.enabled,custom_fields.enum_options,custom_fields.enum_options.color,custom_fields.enum_options.enabled,custom_fields.enum_options.name,custom_fields.enum_value,custom_fields.enum_value.color,custom_fields.enum_value.enabled,custom_fields.enum_value.name,custom_fields.format,custom_fields.has_notifications_enabled,custom_fields.id_prefix,custom_fields.is_formula_field,custom_fields.is_global_to_workspace,custom_fields.is_value_read_only,custom_fields.multi_enum_values,custom_fields.multi_enum_values.color,custom_fields.multi_enum_values.enabled,custom_fields.multi_enum_values.name,custom_fields.name,custom_fields.number_value,custom_fields.people_value,custom_fields.people_value.name,custom_fields.precision,custom_fields.representation_type,custom_fields.resource_subtype,custom_fields.text_value,custom_fields.type,dependencies,dependents,due_at,due_on,external,external.data,followers,followers.name,hearted,hearts,hearts.user,hearts.user.name,html_notes,is_rendered_as_separator,liked,likes,likes.user,likes.user.name,memberships,memberships.project,memberships.project.name,memberships.section,memberships.section.name,modified_at,name,notes,num_hearts,num_likes,num_subtasks,offset,parent,parent.created_by,parent.name,parent.resource_subtype,path,permalink_url,projects,projects.name,resource_subtype,start_at,start_on,tags,tags.name,uri,workspace,workspace.name", # list[str] | This endpoint returns a compact resource, which excludes some properties by default. To include those optional properties, set this query parameter to a comma-separated list of the properties you wish to include.
        }
        tasks = []
        try:
            # Get tasks from a project
            api_response = tasks_api_instance.get_tasks_for_project(self.gid, opts)
            for data in api_response:
                tasks.append(Task.from_data(data, parent=self))
        except ApiException as e:
            print("Exception when calling TasksApi->get_tasks_for_project: %s\n" % e)
        self.tasks = tasks
        return self.tasks

class Workspace(SavableHierEntity):
    def __init__(self, gid: str, name: str, raw_data: dict = None):
        self.gid = gid
        self.raw_data = raw_data
        self.projects = []
        super().__init__(name, None, raw_data)
    
    @staticmethod
    def from_data(data: dict):
        return Workspace(data['gid'], data['name'], raw_data=data)
    
    def get_all(self):
        projects = self.get_projects()
        for prj in projects:
            prj.get_all()
    
    def get_projects(self) -> list[Project]:
        if AsanaExporter.api_client is None:
            raise Exception("No asana api client defined")
        # create an instance of the API class
        projects_api_instance = asana.ProjectsApi(AsanaExporter.api_client)
        opts = {
            'limit': 50, # int | Results per page. The number of objects to return per page. The value must be between 1 and 100.
            # 'offset': "eyJ0eXAiOJiKV1iQLCJhbGciOiJIUzI1NiJ9", # str | Offset token. An offset to the next page returned by the API. A pagination request will return an offset token, which can be used as an input parameter to the next request. If an offset is not passed in, the API will return the first page of results. *Note: You can only pass in an offset that was returned to you via a previously paginated request.*
            # 'archived': False, # bool | Only return projects whose `archived` field takes on the value of this parameter.
            'opt_fields': "archived,color,created_at,default_access_level,default_view,due_date,due_on,followers,followers.name,html_notes,icon,members,members.name,modified_at,name,notes,offset,owner,permalink_url,privacy_setting,project_brief,public,team,team.name,workspace,workspace.name", # list[str] | This endpoint returns a compact resource, which excludes some properties by default. To include those optional properties, set this query parameter to a comma-separated list of the properties you wish to include.
        }
        projects = []
        try:
            # Get all projects in a workspace
            api_response = projects_api_instance.get_projects_for_workspace(self.gid, opts)
            for data in api_response:
                projects.append(Project.from_data(data, parent=self))
        except ApiException as e:
            print("Exception when calling ProjectsApi->get_projects_for_workspace: %s\n" % e)
        self.projects = projects
        return self.projects
    
    def __repr__(self):
        return f"Workspace({self.gid=}, {self.name=})"
    
    @staticmethod
    def get_workspaces() -> list[Self]:
        if AsanaExporter.api_client is None:
            raise Exception("No asana api client defined")
        workspaces_api_instance = asana.WorkspacesApi(AsanaExporter.api_client)
        opts = {
            'limit': 50, # int | Results per page. The number of objects to return per page. The value must be between 1 and 100.
            # 'offset': "eyJ0eXAiOJiKV1iQLCJhbGciOiJIUzI1NiJ9", # str | Offset token. An offset to the next page returned by the API. A pagination request will return an offset token, which can be used as an input parameter to the next request. If an offset is not passed in, the API will return the first page of results. *Note: You can only pass in an offset that was returned to you via a previously paginated request.*
            'opt_fields': "name,offset,path,uri", # list[str] | This endpoint returns a compact resource, which excludes some properties by default. To include those optional properties, set this query parameter to a comma-separated list of the properties you wish to include.
        }
        workspaces = []
        try:
            # Get multiple workspaces
            api_response = workspaces_api_instance.get_workspaces(opts)
            for data in api_response:
                workspaces.append(Workspace.from_data(data))
        except ApiException as e:
            print("Exception when calling WorkspacesApi->get_workspaces: %s\n" % e)
        return workspaces

class AsanaExporter:
    api_client = None
    def __init__(self):
        pass
    def exportAll(self):
        workspaces = Workspace.get_workspaces()
        print(workspaces)
        for ws in workspaces[0:1]:
            print(f"=====================\n{ws}\n=====================")
            ws.get_all()


configuration = asana.Configuration()
configuration.access_token = os.getenv("ASANA_TOKEN")
AsanaExporter.api_client = asana.ApiClient(configuration)

env = Environment(
    loader=FileSystemLoader("templates"),
    autoescape=select_autoescape()
)

template = env.get_template("index.html")
exporter = AsanaExporter()
# exporter.exportAll()
ws = Workspace.get_workspaces()
print(template.render(workspaces=ws))
# for w in ws[:1]:
#     w.save_raw()
#     projects = w.get_projects()
#     for prj in projects[:1]:
#         prj.save_raw()
#         tasks = prj.get_tasks()
#         for tsk in tasks[:10]:
#             tsk.save_raw()
#             stories = tsk.get_stories()
#             for s in stories:
#                 s.save_raw()
#             atts = tsk.get_attachments()
#             for att in atts:
#                 att.save_raw()
#                 att.save()