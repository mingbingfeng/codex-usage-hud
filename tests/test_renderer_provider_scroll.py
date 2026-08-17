from codex_usage_hud import renderer_client as renderer_hud


def test_settings_open_reveals_selected_provider_after_navigation_layout() -> None:
    script = renderer_hud.RENDERER_HUD_SCRIPT
    modal_reveal = (
        'revealSettingsProviderTab(nextDialog?.querySelector('
        "'[data-provider-tab=\"true\"][aria-selected=\"true\"]'))"
    )

    assert "const tabRect = tab.getBoundingClientRect();" in script
    assert "const railRect = tabs.getBoundingClientRect();" in script
    assert "const tabCenter = tabs.scrollLeft + (tabRect.left - railRect.left)" in script
    assert "const nextLeft = tabCenter - (tabs.clientWidth / 2);" in script
    assert modal_reveal in script

    modal_visible = script.index("modal.hidden = false;")
    first_sync = script.index(
        "syncSettingsProviderTabNavigation(nextDialog);", modal_visible
    )
    reveal = script.index(modal_reveal, first_sync)
    second_sync = script.index(
        "syncSettingsProviderTabNavigation(nextDialog);", reveal
    )
    assert modal_visible < first_sync < reveal < second_sync


def test_provider_click_reveals_selected_tab_after_navigation_buttons_resize_rail() -> None:
    script = renderer_hud.RENDERER_HUD_SCRIPT
    editor_start = script.index("function renderSettingsProviderEditor")
    editor_end = script.index("function switchSettingsProvider", editor_start)
    editor_script = script[editor_start:editor_end]

    first_sync = editor_script.index("syncSettingsProviderTabNavigation(editor);")
    reveal = editor_script.index("revealSettingsProviderTab(activeTab);", first_sync)
    second_sync = editor_script.index(
        "syncSettingsProviderTabNavigation(editor);", reveal
    )
    assert first_sync < reveal < second_sync

    switch_start = editor_end
    switch_end = script.index("function activateSettingsProviderTab", switch_start)
    switch_script = script[switch_start:switch_end]
    assert "revealSettingsProviderTab(activeTab);" in switch_script
    assert "scrollSettingsProviderRail(railDirection);" not in switch_script
