# -*- coding: utf-8 -*-

# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file, You can
# obtain one at http://mozilla.org/MPL/2.0/.

import attr
import re

from tcadmin.resources import Role, Client, WorkerPool, Secret, Hook
from tcadmin.util.config import ConfigDict
from .loader import loader
from .workers import build_worker_pool
from .grants import Grants

ADMIN_ROLE_PREFIXES = [
    "github-org-admin:",
    "github-team:",
    "login-identity:",
]


class Projects(ConfigDict):

    filename = "config/projects.yml"

    @attr.s
    class Item:
        name = attr.ib(type=str)
        adminRoles = attr.ib(type=list, factory=lambda: [])
        repos = attr.ib(type=list, factory=lambda: [])
        workerPools = attr.ib(type=dict, factory=lambda: {})
        clients = attr.ib(type=dict, factory=lambda: {})
        grants = attr.ib(type=list, factory=lambda: [])
        secrets = attr.ib(type=dict, factory=lambda: {})
        hooks = attr.ib(type=dict, factory=lambda: {})
        externallyManaged = attr.ib(type=bool, default=False)


async def update_resources(resources):
    projects = await Projects.load(loader)

    for project in projects.values():
        for roleId in project.adminRoles:
            assert any(roleId.startswith(p) for p in ADMIN_ROLE_PREFIXES)
            resources.add(
                Role(
                    roleId=roleId,
                    description="",
                    scopes=["assume:project-admin:{}".format(project.name)],
                )
            )
        if project.repos:
            for repo in project.repos:
                assert repo.endswith("/*") or repo.endswith(
                    ":*"
                ), "project.repos should end with `/*` or `:*`, got {}".format(repo)
            resources.add(
                Role(
                    roleId="project-admin:{}".format(project.name),
                    description="",
                    scopes=[
                        "assume:repo-admin:{}".format(repo) for repo in project.repos
                    ],
                )
            )
        if project.workerPools:
            for name, worker_pool in project.workerPools.items():
                worker_pool_id = "proj-{}/{}".format(project.name, name)
                if project.externallyManaged:
                    resources.manage("WorkerPool={}".format(worker_pool_id))
                    resources.manage("Secret=worker-pool:{}".format(worker_pool_id))
                worker_pool["description"] = "Workers for " + project.name
                resources.add(build_worker_pool(worker_pool_id, worker_pool))
                resources.add(Secret(name="worker-pool:{}".format(worker_pool_id)))
        if project.clients:
            for name, info in project.clients.items():
                clientId = "project/{}/{}".format(project.name, name)
                if project.externallyManaged:
                    resources.manage("Client={}".format(client_id))
                description = info.get("description", "")
                scopes = info["scopes"]
                resources.add(
                    Client(clientId=clientId, description=description, scopes=scopes)
                )
        if project.secrets:
            for nameSuffix, info in project.secrets.items():
                assert info is True, "secrets must have the form <nameSuffix>: true"
                name = "project/{}/{}".format(project.name, nameSuffix)
                if project.externallyManaged:
                    resources.manage("Secret={}".format(name))
                resources.add(Secret(name=name))
        if project.hooks:
            for hookId, info in project.hooks.items():
                hookGroupId = "project-{}".format(project.name)
                if project.externallyManaged:
                    resources.manage("Hook={}/{}".format(hookGroupid, hookId))
                assert (
                    "bindings" not in info
                ), "Please add support for bindings to use this feature"
                resources.add(
                    Hook(
                        hookGroupId=hookGroupId,
                        hookId=hookId,
                        name=info.get("name", hookId),
                        description=info.get("description", ""),
                        owner=info["owner"],
                        emailOnError=info.get("emailOnError", False),
                        schedule=info.get("schedule", ()),
                        bindings=info.get("bindings", ()),
                        task=info["task"],
                        triggerSchema=info.get("triggerSchema", {}),
                    )
                )
        for grant in Grants.from_project(project):
            grant.update_resources(resources)


async def get_externally_managed_resource_patterns():
    """Get a list of regular expressions for resources that are externally
    managed"""
    projects = await Projects.load(loader)
    patterns = []
    for project in projects.values():
        if not project.externallyManaged:
            continue
        name = re.escape(project.name)

        # this list corresponds to that for project-admin:* in
        # config/grants.yml
        patterns.append(r"Role=project:{}:.*".format(name))
        patterns.append(r"Client=project/{}/.*".format(name))
        patterns.append(r"WorkerPool=proj-{}/.*".format(name))
        patterns.append(r"Secret=worker-pool:proj-{}/.*".format(name))
        patterns.append(r"Hook=project-{}/.*".format(name))
        patterns.append(r"Role=hook-id:project-{}/.*".format(name))
        patterns.append(r"Secret=project/{}/.*".format(name))

        # this corresponds to repo-admin:*
        for repo in project.repos:
            pat = (re.escape(repo[:-1]) + ".*") if repo[-1] == "*" else re.escape(repo)
            patterns.append(r"Role=repo:" + pat)

    return patterns
