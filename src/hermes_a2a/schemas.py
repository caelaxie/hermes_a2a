"""Hermes tool schemas for the A2A bridge."""

A2A_STATUS_SCHEMA = {
    "name": "a2a_status",
    "description": (
        "Return the local Hermes A2A bridge status, including effective config, "
        "published agent card details, and local task counts."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

A2A_LIST_AGENTS_SCHEMA = {
    "name": "a2a_list_agents",
    "description": (
        "List configured remote A2A agents known to Hermes. Use this before "
        "delegating to a remote alias."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

A2A_GET_TASK_SCHEMA = {
    "name": "a2a_get_task",
    "description": (
        "Fetch a task snapshot by id. This checks local persisted state first and "
        "refreshes remote state when the task represents outbound delegation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Task id to fetch"},
        },
        "required": ["task_id"],
    },
}

A2A_CANCEL_TASK_SCHEMA = {
    "name": "a2a_cancel_task",
    "description": (
        "Cancel a local or remotely delegated A2A task by id."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Task id to cancel"},
        },
        "required": ["task_id"],
    },
}

A2A_DELEGATE_SCHEMA = {
    "name": "a2a_delegate",
    "description": (
        "Send a user message to a remote A2A agent using either a configured alias "
        "or a direct URL. Supports wait, stream, and poll delivery modes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "Remote agent alias or absolute URL",
            },
            "message": {
                "type": "string",
                "description": "Message text to send to the remote A2A agent",
            },
            "mode": {
                "type": "string",
                "description": "Delegation mode: wait, stream, or poll",
                "enum": ["wait", "stream", "poll"],
            },
            "task_id": {
                "type": "string",
                "description": "Optional existing task id for continuation",
            },
            "context_id": {
                "type": "string",
                "description": "Optional context id for the delegated task",
            },
        },
        "required": ["target", "message"],
    },
}
