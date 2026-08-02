"""Gemma Studio backend."""

import warnings

# LangGraph emits this while importing an unused optional cache serializer. Devvy does not
# construct that serializer, so there is no local ``allowed_objects`` value to configure.
warnings.filterwarnings(
    "ignore",
    message=r"The default value of `allowed_objects` will change.*",
    category=Warning,
    module=r"langgraph\.cache\.base.*",
)
