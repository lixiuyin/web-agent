"""Tool-surface definitions shared by browser-grounded benchmark suites."""

BROWSER_ONLY_TOOLS = frozenset(
    {
        "back",
        "click",
        "click_link",
        "close_tab",
        "done",
        "download_file",
        "frame_interact",
        "forward",
        "get_all_links",
        "get_attribute",
        "get_element_text",
        "get_title",
        "get_url",
        "goto",
        "hover",
        "list_frames",
        "list_tabs",
        "open_tab",
        "press",
        "remember",
        "refresh",
        "scroll",
        "scroll_to_element",
        "select_dropdown",
        "shadow_dom",
        "switch_tab",
        "type",
        "upload_file",
        "wait",
        "wait_for_element",
    }
)

__all__ = ["BROWSER_ONLY_TOOLS"]
