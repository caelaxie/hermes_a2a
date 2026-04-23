"""Tool schemas exposed to Hermes."""

A2A_STATUS_SCHEMA = {
    "name": "a2a_status",
    "description": (
        "Return deployment and configuration status for the Hermes A2A plugin scaffold. "
        "Use this to verify that the plugin is installed and to inspect whether "
        "basic A2A environment variables have been configured."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}
