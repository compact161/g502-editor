var plugin = 'io.github.compact161.g502onboardeditor.buttonmap';
panels().forEach(function(panel) {
    panel.widgets().forEach(function(widget) {
        if (widget.type !== 'org.kde.plasma.systemtray') return;
        widget.currentConfigGroup = ['General'];
        var raw = widget.readConfig('extraItems', []);
        var entries = Array.isArray(raw) ? raw : String(raw).split(',').filter(Boolean);
        if (entries.indexOf(plugin) < 0) entries.push(plugin);
        widget.writeConfig('extraItems', entries);
        print('G502 button map enabled in the system tray: panel ' + panel.id);
    });
});
