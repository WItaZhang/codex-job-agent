"""Two actual update protocols, sharing a model bridge and profile contract."""

import json

from .profile_memory_cases import Profile


def update_profile(arm: str, model, current: Profile, message: dict, instructions: str, max_steps: int) -> Profile:
    from langchain_core.messages import HumanMessage, SystemMessage

    # Trustcall's update-only tool is PatchDoc: its tool schema does not carry
    # Profile's field descriptions. Supply identical domain semantics to both arms.
    instructions += "\nShared profile schema:\n" + json.dumps(Profile.model_json_schema(), ensure_ascii=False)
    if arm == "codex_direct":
        response = model.bind_tools([Profile], tool_choice="Profile").invoke(
            [
                SystemMessage(content=instructions + " Return the complete updated Profile in one Profile tool call."),
                HumanMessage(
                    content=json.dumps(
                        {"current_profile": current.model_dump(exclude_unset=True), "new_input": message},
                        ensure_ascii=False,
                    )
                ),
            ]
        )
        if len(response.tool_calls) != 1 or response.tool_calls[0]["name"] != "Profile":
            raise ValueError("Direct updater must return exactly one Profile tool call")
        return Profile.model_validate(response.tool_calls[0]["args"])
    if arm != "langmem_profile":
        raise ValueError(f"Unknown experimental arm: {arm}")

    from langmem import create_memory_manager

    manager = create_memory_manager(
        model,
        schemas=[Profile],
        instructions=instructions,
        enable_inserts=False,
        enable_updates=True,
        enable_deletes=False,
    )
    # Always pass only the previous predicted state and new input, never oracle state.
    memories = manager.invoke(
        {
            "messages": [HumanMessage(content=json.dumps(message, ensure_ascii=False))],
            # Explicit schema + JSON preserves absent optional override fields.
            "existing": [("current-profile", "Profile", current.model_dump(exclude_unset=True))],
            "max_steps": max_steps,
        }
    )
    if len(memories) != 1 or memories[0].id != "current-profile":
        raise ValueError("LangMem must preserve exactly one existing profile identity")
    return Profile.model_validate(memories[0].content)
