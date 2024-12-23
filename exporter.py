import asana
from asana.rest import ApiException
from pprint import pprint
import os
from dotenv import load_dotenv
from typing import Self, Type
import json
from pathlib import Path
from slugify import slugify
import requests
from tqdm import tqdm
import humanize
from jinja2 import Environment, FileSystemLoader, select_autoescape
import logging
import re
import locale
import argparse

parser = argparse.ArgumentParser(
            prog="Asana exporter",
            description="Exports all workspaces you are part of to HTML",
            )
parser.add_argument("-d", "--download-attachments", type=bool, default=True, help="default=True")
parser.add_argument("-r", "--save-raw-responses", type=bool, default=True, help="default=True")
parser.add_argument("-e", "--export-html", type=bool, default=True, help="default=True")
parser.add_argument("-o", "--output-dir", default="out/", help="default=./out/")
parser.add_argument("-s", "--separate-responses", action='store_true', help="store HTML and json files in separate directories")
parser.add_argument("--load-local-responses", action='store_true', help="load raw responses from previous runs")
parser.add_argument("-l", "--locale", help="set locale - needed for locale aware sorting")
parser.add_argument("--log-file", default="app.log")

logger = logging.getLogger(__name__)

default_base_path = Path("out/")

class ExportConfig:
    def __init__(self, api_client, output_dir: Path|str, save_raw:bool, separate_raw: bool, export_html: bool, download_attachments: bool, html_templates: list):
        self.api_client = api_client
        self.save_raw = save_raw
        if isinstance(output_dir, str):
            output_dir = Path(output_dir)
        if separate_raw:
            self.raw_base_path = output_dir / "json"
            self.html_base_path = output_dir / "html"
        else:
            self.raw_base_path = output_dir
            self.html_base_path = output_dir
        self.export_html = export_html
        self.download_attachments = download_attachments
        self.html_templates = html_templates

class SavableHierEntity:
    def __init__(self, cfg: ExportConfig, gid: str, name: str, parent: Self, raw_data:dict = None):
        self.cfg = cfg
        self.gid = gid
        self.name = name
        self.parent = parent
        self.raw_data = raw_data
    
    def filename(self, extension: str = ""):
        name = str(self.name)
        return slugify(name if len(name) > 0 else str(self.gid)) + extension
    
    def path(self, base_path=default_base_path) -> Path:
        obj = self.parent
        obj_path = []
        while obj is not None and isinstance(obj, SavableHierEntity):
            obj_path.append(obj)
            obj = obj.parent
        obj_path.reverse()
        path = base_path
        if not isinstance(base_path, Path):
            path = Path(sanitize_filepath(base_path))
        for o in obj_path:
            path = path / o.filename()
        return path
    
    def get_save_path(self, extension="", base_path=default_base_path):
        return self.path(base_path=base_path) / self.filename(extension=extension)
    
    def save_raw(self):
        path = self.get_save_path(".json", base_path=self.cfg.raw_base_path)
        if not path.parent.exists():
            path.parent.mkdir(parents=True)
        try:
            with open(path, mode="w") as f:
                json.dump(self.raw_data, f, indent=2)
        except (OSError,FileNotFoundError):
            logger.warn(f"{self} save_raw: \"{path}\" is not a valid path")
    
    def export_html(self, template):
        save_path = self.get_save_path(base_path=self.cfg.html_base_path) / "index.html"
        if not save_path.parent.exists():
            save_path.parent.mkdir(parents=True)
        try:
            with open(save_path, mode="w") as f:
                f.write(template.render(data=self))
        except (OSError,FileNotFoundError):
            logger.warn(f"{self} save_raw: \"{save_path}\" is not a valid path")

class Attachment(SavableHierEntity):
    save_dir = "attachments"
    def __init__(self, cfg: ExportConfig, gid: str, name: str, download_url: str, created_at: str, size: int, resource_subtype: str, parent: 'Task' = None, raw_data: dict = None):
        self.download_url = download_url
        self.created_at = created_at
        self.size = size
        self.resource_subtype = resource_subtype
        super().__init__(cfg, gid, name, parent, raw_data)
    
    def from_data(data: dict, cfg: ExportConfig, parent = None):
        return Attachment(cfg, data["gid"], data["name"], data["download_url"], data["created_at"], data.get("size"), data["resource_subtype"], parent=parent, raw_data=data)
    
    def path(self, base_path=default_base_path) -> Path:
        return super().path(base_path=base_path) / self.save_dir
    
    def save(self):
        if self.download_url is None:
            if self.resource_subtype == "asana":
                raise Exception("Download URL of an attachment is not specified")
            logger.warning(f"{self} no download url - skipping download")
            return
        if self.name is None:
            raise Exception("Name of an attachment is not specified")
        save_path = self.path(base_path=self.cfg.html_base_path) / self.name
        if not save_path.parent.exists():
            save_path.parent.mkdir(parents=True)
        resp = requests.get(self.download_url, stream=True)
        size = int(resp.headers.get('content-length', 0))
        block_size = 1024
        size_str = ""
        if self.size is not None:
            size_str = f" ({humanize.naturalsize(self.size, binary=True)})"
        print(f"Downloading {self.name}{size_str}")
        with tqdm(total=size, unit="B", unit_scale=True) as progress_bar:
            with open(save_path, mode="wb") as f:
                for data in resp.iter_content(block_size):
                    progress_bar.update(len(data))
                    f.write(data)

# Story is a comment on task or an update message
class Story(SavableHierEntity):
    save_dir = "stories"
    # TODO: how are represented attachments within comments
    def __init__(self, cfg: ExportConfig, gid: str, story_type: str, likes: list, text: str, created_at: str, username: str = None, parent: Self = None, raw_data: dict = None):
        self.story_type = story_type
        self.likes = likes
        self.text = text
        self.created_at = created_at
        self.username = username
        super().__init__(cfg, gid, "story_"+str(gid), parent, raw_data)
    
    @staticmethod
    def from_data(data: dict, cfg: ExportConfig, parent = None):
        username = None
        if data["type"] == "comment":
            username = data["created_by"]["name"]
        
        return Story(cfg, data["gid"], data["type"], data.get("likes"), data["html_text"], data["created_at"], username=username, parent=parent, raw_data=data)
    
    def path(self, base_path=default_base_path) -> (Path, str):
        return super().path(base_path=base_path) / self.save_dir

class Task(SavableHierEntity):
    def __init__(self, cfg: ExportConfig, gid: str, name: str, due_at: str, due_on: str, followers: list, notes: str, num_subtasks: int, tags: list, memberships: list, parent: Self | 'Project' = None, raw_data: dict = None):
        self.due_at = due_at
        self.due_on = due_on
        self.followers = followers
        self.notes = notes
        self.num_subtasks = num_subtasks
        self.subtasks = []
        self.tags = tags
        self.stories = []
        self.attachments = []
        self.memberships = memberships
        self.name_xfrm = locale.strxfrm(name)
        super().__init__(cfg, gid, name, parent, raw_data)
    
    def __repr__(self):
        return f"Task(\n\t{self.gid=},\n\t{self.name=},\n\t{self.due_at=},\n\t{self.due_on=},\n\t{self.followers=},\n\t{self.notes=},\n\t{self.num_subtasks=},\n\t{self.subtasks=},\n\t{self.tags=},\n\t{self.memberships=},\n\t{len(self.stories)=}\n\t)"
    
    def from_data(data: dict, cfg: ExportConfig, parent = None):
        return Task(cfg, data["gid"], data["name"], data["due_at"], data["due_on"], data["followers"], data["html_notes"], data["num_subtasks"], data["tags"], data["memberships"], parent=parent, raw_data=data)
    
    def get_all(self):
        logger.info(f"{self} getting stories")
        self.get_stories()
        logger.debug(f"{self} {self.stories=}")
        logger.info(f"{self} getting attachments")
        self.get_attachments()
        logger.debug(f"{self} {self.attachments=}")
        logger.info(f"{self} getting subtasks")
        subtasks = self.get_subtasks()
        logger.debug(f"{self} {self.subtasks=}")
        for sub in subtasks:
            sub.get_all()
    
    def save_raw_rec(self):
        self.save_raw()
        for tsk in self.subtasks:
            tsk.save_raw_rec()
        for story in self.stories:
            story.save_raw_rec()
        for atch in self.attachments:
            atch.save_raw_rec()
    
    def export(self):
        self.export_html(template=self.cfg.html_templates[self.__class__.__name__])

    def get_stories(self) -> list[Story]:
        if self.cfg is None or self.cfg.api_client is None:
            raise Exception("No asana api client defined")
        # create an instance of the API class
        stories_api_instance = asana.StoriesApi(self.cfg.api_client)
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
                logger.debug(f"{self} story-data={data}")
                story = Story.from_data(data, self.cfg, parent=self)
                stories.append(story)
                if self.cfg.save_raw:
                    story.save_raw()
        except ApiException as e:
            print("Exception when calling StoriesApi->get_stories_for_task: %s\n" % e)
        self.stories = stories
        return stories
    
    def get_attachments(self) -> list[Attachment]:
        if self.cfg is None or self.cfg.api_client is None:
            raise Exception("No asana api client defined")
        # create an instance of the API class
        attachments_api_instance = asana.AttachmentsApi(self.cfg.api_client)
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
                logger.debug(f"{self} attachment-data={data}")
                atch = Attachment.from_data(data, self.cfg, parent=self)
                attachments.append(atch)
                if self.cfg.save_raw:
                    atch.save_raw()
                if self.cfg.download_attachments:
                    atch.save()
        except ApiException as e:
            print("Exception when calling AttachmentsApi->get_attachments_for_object: %s\n" % e)
        self.attachments = attachments
        return self.attachments
    
    def get_subtasks(self) -> list[Self]:
        if self.cfg is None or self.cfg.api_client is None:
            raise Exception("No asana api client defined")
        # create an instance of the API class
        tasks_api_instance = asana.TasksApi(self.cfg.api_client)
        opts = {
            'limit': 50, # int | Results per page. The number of objects to return per page. The value must be between 1 and 100.
            # 'offset': "eyJ0eXAiOJiKV1iQLCJhbGciOiJIUzI1NiJ9", # str | Offset token. An offset to the next page returned by the API. A pagination request will return an offset token, which can be used as an input parameter to the next request. If an offset is not passed in, the API will return the first page of results. *Note: You can only pass in an offset that was returned to you via a previously paginated request.*
            'opt_fields': "actual_time_minutes,approval_status,assignee,assignee.name,assignee_section,assignee_section.name,assignee_status,completed,completed_at,completed_by,completed_by.name,created_at,created_by,custom_fields,custom_fields.asana_created_field,custom_fields.created_by,custom_fields.created_by.name,custom_fields.currency_code,custom_fields.custom_label,custom_fields.custom_label_position,custom_fields.date_value,custom_fields.date_value.date,custom_fields.date_value.date_time,custom_fields.description,custom_fields.display_value,custom_fields.enabled,custom_fields.enum_options,custom_fields.enum_options.color,custom_fields.enum_options.enabled,custom_fields.enum_options.name,custom_fields.enum_value,custom_fields.enum_value.color,custom_fields.enum_value.enabled,custom_fields.enum_value.name,custom_fields.format,custom_fields.has_notifications_enabled,custom_fields.id_prefix,custom_fields.is_formula_field,custom_fields.is_global_to_workspace,custom_fields.is_value_read_only,custom_fields.multi_enum_values,custom_fields.multi_enum_values.color,custom_fields.multi_enum_values.enabled,custom_fields.multi_enum_values.name,custom_fields.name,custom_fields.number_value,custom_fields.people_value,custom_fields.people_value.name,custom_fields.precision,custom_fields.representation_type,custom_fields.resource_subtype,custom_fields.text_value,custom_fields.type,dependencies,dependents,due_at,due_on,external,external.data,followers,followers.name,hearted,hearts,hearts.user,hearts.user.name,html_notes,is_rendered_as_separator,liked,likes,likes.user,likes.user.name,memberships,memberships.project,memberships.project.name,memberships.section,memberships.section.name,modified_at,name,notes,num_hearts,num_likes,num_subtasks,offset,parent,parent.created_by,parent.name,parent.resource_subtype,path,permalink_url,projects,projects.name,resource_subtype,start_at,start_on,tags,tags.name,uri,workspace,workspace.name", # list[str] | This endpoint returns a compact resource, which excludes some properties by default. To include those optional properties, set this query parameter to a comma-separated list of the properties you wish to include.
        }
        subtasks = []
        try:
            # Get tasks from a project
            api_response = tasks_api_instance.get_subtasks_for_task(self.gid, opts)
            for data in api_response:
                logger.debug(f"{self} subtask-data={data}")
                tsk = Task.from_data(data, self.cfg, parent=self)
                subtasks.append(tsk)
                if self.cfg.save_raw:
                    tsk.save_raw()
        except ApiException as e:
            print("Exception when calling TasksApi->get_subtasks_for_task: %s\n" % e)
        self.subtasks = subtasks
        return self.subtasks
    
    def load_from_raw(self):
        path, name = self.path(base_path=self.cfg.raw_base_path), self.filename()
        task_path = path / name
        subtask_files = task_path.glob("*.json")
        for subtask_file in subtask_files:
            with open(subtask_file) as f:
                data = json.load(f)
                subtask = Task.from_data(data, self.cfg, parent=self)
                self.subtasks.append(subtask)
                subtask.load_from_raw()
        stories_path = task_path / Story.save_dir
        story_files = stories_path.glob("*.json")
        for story_file in story_files:
            with open(story_file) as f:
                data = json.load(f)
                self.stories.append(Story.from_data(data, self.cfg, parent=self))
        attachments_path = task_path / Attachment.save_dir
        atch_files = attachments_path.glob("*.json")
        for atch_file in atch_files:
            with open(atch_file) as f:
                data = json.load(f)
                self.attachments.append(Attachment.from_data(data, self.cfg, parent=self))

class Project(SavableHierEntity):
    def __init__(self, cfg: ExportConfig, gid: str, name: str, color: str, modified_at: str, parent: 'Workspace' = None, raw_data: dict = None):
        self.color = color
        self.modified_at = modified_at
        self.tasks = []
        super().__init__(cfg, gid, name, parent, raw_data)
    
    def __repr__(self):
        return f"Project(\n\t{self.gid=},\n\t{self.name=},\n\t{self.color=},\n\t{self.modified_at=}\n\t)"
    
    @staticmethod
    def from_data(data: dict, cfg: ExportConfig, parent = None):
        return Project(cfg, data["gid"], data["name"], data["color"], data["modified_at"], parent=parent, raw_data=data)
    
    def get_all(self):
        logger.info(f"{self} getting tasks")
        tasks = self.get_tasks()
        logger.debug(f"{self} {self.tasks=}")
        for tsk in tasks:
            tsk.get_all()
    
    def save_raw_rec(self):
        self.save_raw()
        for tsk in self.tasks:
            tsk.save_raw_rec()
    
    def export(self):
        self.export_html(template=self.cfg.html_templates[self.__class__.__name__])
        for tsk in self.tasks:
            tsk.export()
    
    def get_tasks(self):
        if self.cfg is None or self.cfg.api_client is None:
            raise Exception("No asana api client defined")
        # create an instance of the API class
        tasks_api_instance = asana.TasksApi(self.cfg.api_client)
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
                logger.debug(f"{self} task-data={data}")
                tsk = Task.from_data(data, self.cfg, parent=self)
                tasks.append(tsk)
                if self.cfg.save_raw:
                    tsk.save_raw()
        except ApiException as e:
            print("Exception when calling TasksApi->get_tasks_for_project: %s\n" % e)
        self.tasks = tasks
        return self.tasks
    
    def load_from_raw(self):
        path, name = self.path(base_path=self.cfg.raw_base_path), self.filename()
        tasks_path = path / name
        task_files = tasks_path.glob("*.json")
        for tsk_file in task_files:
            with open(tsk_file) as f:
                data = json.load(f)
                task = Task.from_data(data, self.cfg, parent=self)
                self.tasks.append(task)
                task.load_from_raw()

class Workspace(SavableHierEntity):
    def __init__(self, cfg: ExportConfig, gid: str, name: str, raw_data: dict = None):
        self.raw_data = raw_data
        self.projects = []
        super().__init__(cfg, gid, name, None, raw_data)
    
    @staticmethod
    def from_data(data: dict, cfg: ExportConfig):
        return Workspace(cfg, data['gid'], data['name'], raw_data=data)
    
    def get_all(self):
        logger.info(f"{self} getting projects")
        projects = self.get_projects()
        logger.debug(f"{self} {self.projects=}")
        for prj in projects:
            prj.get_all()
    
    def save_raw_rec(self):
        self.save_raw()
        for prj in self.projects:
            prj.save_raw_rec()
    
    def export(self):
        self.export_html(template=self.cfg.html_templates[self.__class__.__name__])
        for prj in self.projects:
            prj.export()
    
    def get_projects(self) -> list[Project]:
        if self.cfg is None or self.cfg.api_client is None:
            raise Exception("No asana api client defined")
        # create an instance of the API class
        projects_api_instance = asana.ProjectsApi(self.cfg.api_client)
        opts = {
            'limit': 50, # int | Results per page. The number of objects to return per page. The value must be between 1 and 100.
            # 'offset': "eyJ0eXAiOJiKV1iQLCJhbGciOiJIUzI1NiJ9", # str | Offset token. An offset to the next page returned by the API. A pagination request will return an offset token, which can be used as an input parameter to the next request. If an offset is not passed in, the API will return the first page of results. *Note: You can only pass in an offset that was returned to you via a previously paginated request.*
            # 'archived': False, # bool | Only return projects whose `archived` field takes on the value of this parameter.
            'opt_fields': "archived,color,completed,completed_at,completed_by,completed_by.name,created_at,created_from_template,created_from_template.name,current_status,current_status.author,current_status.author.name,current_status.color,current_status.created_at,current_status.created_by,current_status.created_by.name,current_status.html_text,current_status.modified_at,current_status.text,current_status.title,current_status_update,current_status_update.resource_subtype,current_status_update.title,custom_field_settings,custom_field_settings.custom_field,custom_field_settings.custom_field.asana_created_field,custom_field_settings.custom_field.created_by,custom_field_settings.custom_field.created_by.name,custom_field_settings.custom_field.currency_code,custom_field_settings.custom_field.custom_label,custom_field_settings.custom_field.custom_label_position,custom_field_settings.custom_field.date_value,custom_field_settings.custom_field.date_value.date,custom_field_settings.custom_field.date_value.date_time,custom_field_settings.custom_field.description,custom_field_settings.custom_field.display_value,custom_field_settings.custom_field.enabled,custom_field_settings.custom_field.enum_options,custom_field_settings.custom_field.enum_options.color,custom_field_settings.custom_field.enum_options.enabled,custom_field_settings.custom_field.enum_options.name,custom_field_settings.custom_field.enum_value,custom_field_settings.custom_field.enum_value.color,custom_field_settings.custom_field.enum_value.enabled,custom_field_settings.custom_field.enum_value.name,custom_field_settings.custom_field.format,custom_field_settings.custom_field.has_notifications_enabled,custom_field_settings.custom_field.id_prefix,custom_field_settings.custom_field.is_formula_field,custom_field_settings.custom_field.is_global_to_workspace,custom_field_settings.custom_field.is_value_read_only,custom_field_settings.custom_field.multi_enum_values,custom_field_settings.custom_field.multi_enum_values.color,custom_field_settings.custom_field.multi_enum_values.enabled,custom_field_settings.custom_field.multi_enum_values.name,custom_field_settings.custom_field.name,custom_field_settings.custom_field.number_value,custom_field_settings.custom_field.people_value,custom_field_settings.custom_field.people_value.name,custom_field_settings.custom_field.precision,custom_field_settings.custom_field.representation_type,custom_field_settings.custom_field.resource_subtype,custom_field_settings.custom_field.text_value,custom_field_settings.custom_field.type,custom_field_settings.is_important,custom_field_settings.parent,custom_field_settings.parent.name,custom_field_settings.project,custom_field_settings.project.name,custom_fields,custom_fields.date_value,custom_fields.date_value.date,custom_fields.date_value.date_time,custom_fields.display_value,custom_fields.enabled,custom_fields.enum_options,custom_fields.enum_options.color,custom_fields.enum_options.enabled,custom_fields.enum_options.name,custom_fields.enum_value,custom_fields.enum_value.color,custom_fields.enum_value.enabled,custom_fields.enum_value.name,custom_fields.id_prefix,custom_fields.is_formula_field,custom_fields.multi_enum_values,custom_fields.multi_enum_values.color,custom_fields.multi_enum_values.enabled,custom_fields.multi_enum_values.name,custom_fields.name,custom_fields.number_value,custom_fields.representation_type,custom_fields.resource_subtype,custom_fields.text_value,custom_fields.type,default_access_level,default_view,due_date,due_on,followers,followers.name,html_notes,icon,members,members.name,minimum_access_level_for_customization,minimum_access_level_for_sharing,modified_at,name,notes,offset,owner,path,permalink_url,privacy_setting,project_brief,public,start_on,team,team.name,uri,workspace,workspace.name", # list[str] | This endpoint returns a compact resource, which excludes some properties by default. To include those optional properties, set this query parameter to a comma-separated list of the properties you wish to include.
        }
        projects = []
        try:
            # Get all projects in a workspace
            api_response = projects_api_instance.get_projects_for_workspace(self.gid, opts)
            for data in api_response:
                logger.debug(f"{self} project-data={data}")
                prj = Project.from_data(data, self.cfg, parent=self)
                projects.append(prj)
                if self.cfg.save_raw:
                    prj.save_raw()
        except ApiException as e:
            print("Exception when calling ProjectsApi->get_projects_for_workspace: %s\n" % e)
        self.projects = projects
        return self.projects
    
    def __repr__(self):
        return f"Workspace({self.gid=}, {self.name=})"
    
    def load_from_raw(self):
        path, name = self.path(base_path=self.cfg.raw_base_path), self.filename()
        projects_path = path / name
        project_files = projects_path.glob("*.json")
        for prj_file in project_files:
            with open(prj_file) as f:
                data = json.load(f)
                project = Project.from_data(data, self.cfg, parent=self)
                self.projects.append(project)
                project.load_from_raw()
    
    @staticmethod
    def get_workspaces(cfg: ExportConfig) -> list[Self]:
        if cfg is None or cfg.api_client is None:
            raise Exception("No asana api client defined")
        workspaces_api_instance = asana.WorkspacesApi(cfg.api_client)
        opts = {
            'limit': 50, # int | Results per page. The number of objects to return per page. The value must be between 1 and 100.
            # 'offset': "eyJ0eXAiOJiKV1iQLCJhbGciOiJIUzI1NiJ9", # str | Offset token. An offset to the next page returned by the API. A pagination request will return an offset token, which can be used as an input parameter to the next request. If an offset is not passed in, the API will return the first page of results. *Note: You can only pass in an offset that was returned to you via a previously paginated request.*
            'opt_fields': "email_domains,is_organization,name,offset,path,uri", # list[str] | This endpoint returns a compact resource, which excludes some properties by default. To include those optional properties, set this query parameter to a comma-separated list of the properties you wish to include.
        }
        workspaces = []
        try:
            # Get multiple workspaces
            api_response = workspaces_api_instance.get_workspaces(opts)
            for data in api_response:
                logger.debug(f"workspace-data={data}")
                workspace = Workspace.from_data(data, cfg)
                if cfg.save_raw:
                    workspace.save_raw()
                workspaces.append(workspace)
        except ApiException as e:
            print("Exception when calling WorkspacesApi->get_workspaces: %s\n" % e)
        return workspaces
class AsanaExporter:
    api_client = None
    def __init__(self, cfg: ExportConfig):
        self.workspaces = []
        self.cfg = cfg
    def getAll(self):
        self.workspaces = Workspace.get_workspaces(self.cfg)
        for ws in self.workspaces:
            if self.cfg.save_raw:
                ws.save_raw()
            ws.get_all()
    def exportAll(self):
        self.export_html(self.cfg.html_templates["index"], path=self.cfg.html_base_path)
        for ws in self.workspaces:
            ws.export()
    def export_html(self, template, path = default_base_path):
        if not path.exists():
            path.mkdir(parents=True)
        with open(path / "index.html", mode="w") as f:
            f.write(template.render(data=self))
    def load_from_raw(self):
        # Raw files are stored alongside the folder which they represent
        # Folder structure:
        # - Workspace/
        #   - Project/
        #       - Task/
        #           - (/Subtask/...)
        #           - stories/
        #           - attachments/
        ws_files = self.cfg.raw_base_path.glob("*.json")
        for ws_file in ws_files:
            with open(ws_file) as f:
                data = json.load(f)
                ws = Workspace.from_data(data, self.cfg)
                self.workspaces.append(ws)
                ws.load_from_raw()


def navigation_relpaths(base_obj: SavableHierEntity):
    navigation = []
    hier_obj = base_obj.parent
    path = Path("..")
    while hier_obj != None:
        navigation.append({ "obj": hier_obj, "path": str(path / "index.html") })
        path = path / ".."
        hier_obj = hier_obj.parent
    # Add overview link
    # (this is kind of a hack, instead of object we pass dictionary - it acts the same way in jinja)
    navigation.append({ "obj": {"name": "Overview"}, "path": str(path / "index.html") })
    return navigation

def remove_bodytag(value):
    value = re.sub(r'^\s*<body>', '', value)
    value = re.sub(r'</body>\s*$', '', value)
    return value

def main(args):
    default_base_path = Path(args.output_dir)
    
    load_dotenv()

    configuration = asana.Configuration()
    configuration.access_token = os.getenv("ASANA_TOKEN")
    api_client = asana.ApiClient(configuration)

    env = Environment(
        loader=FileSystemLoader("templates"),
        autoescape=select_autoescape()
    )

    env.filters["remove_bodytag"] = remove_bodytag
    env.filters["navigation_relpaths"] = navigation_relpaths

    templates = {
        "index": env.get_template("index.html"),
        Workspace.__name__: env.get_template("workspace.html"),
        Project.__name__: env.get_template("project.html"),
        Task.__name__: env.get_template("task.html"),
    }

    cfg = ExportConfig(
        api_client=api_client,
        output_dir=args.output_dir,
        save_raw=args.save_raw_responses,
        separate_raw=args.separate_responses,
        export_html=args.export_html,
        download_attachments=args.download_attachments,
        html_templates=templates
    )

    logging.basicConfig(filename=args.log_file, level=logging.DEBUG)

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(logging.BASIC_FORMAT))

    logging.getLogger('').addHandler(console)

    if args.locale:
        locale.setlocale(locale.LC_ALL, args.locale)

    exporter = AsanaExporter(cfg)
    if args.load_local_responses:
        exporter.load_from_raw()
    else:
        exporter.getAll()
    
    if cfg.export_html:
        exporter.exportAll()

if __name__ == "__main__":
    args = parser.parse_args()
    main(args)