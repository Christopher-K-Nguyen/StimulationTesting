function [needsMonitorChannel,needsCursor] = checkPreviousCursor(File)
%% Constants
% Buttons
BUTTON_YES = 'Yes';
BUTTON_NO = 'No';
opts.Default = BUTTON_NO;
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
needsMonitorChannel = false;
needsCursor = false;

%% Function
questMonitor = questdlg( ...
    'Do you want to change monitor settings?', ...
    'Change Monitor Settings', ...
    BUTTON_YES,BUTTON_NO,BUTTON_NO);
switch questMonitor
    case BUTTON_YES
        questChange = questdlg( ...
            'Change which monitor settings?', ...
            'Change Monitor Settings', ...
            'Channel','Cursor','Both',opts);
        switch questChange
            case 'Channel'
                needsMonitorChannel = true;
                needsCursor = false;
            case 'Cursor'
                needsMonitorChannel = false;
                needsCursor = true;
            case 'Both'
                needsMonitorChannel = true;
                needsCursor = true;
        end
    case BUTTON_NO
        needsMonitorChannel = false;
        needsCursor = false;
end
end

end