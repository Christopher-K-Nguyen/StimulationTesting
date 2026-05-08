function File = getCursorTime2_Tek(File)
%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm';
BUTTON_CANCEL = 'Cancel';
BUTTON_TRY = 'Try Again';
opts.Default = BUTTON_CONFIRM;
opts.Interpreter = 'tex';   % option LaTeX
DIMS = [1 70];

%% Variables
numOfDevices = length(File.Oscilloscope);
cursorTime2_cell = cell(numOfDevices,1);
capture_arr = [File.Data.Index];
captureNum = length(capture_arr);
% Monitor
if captureNum > 1
    prevCaptureNum = captureNum - 1;
else
    prevCaptureNum = 1;
end

%% Function
for deviceNum = 1:numOfDevices
    monitorChannel_cell = File.Data(captureNum).Monitor(deviceNum).Channel;
    hasMonitor = ~isempty(monitorChannel_cell);
    if hasMonitor
    numOfMonitors = length(monitorChannel_cell);
    for monitor_idx = 1:numOfMonitors
        monitorChannel = monitorChannel_cell{monitor_idx};
        cursorTime_cell = File.Data(prevCaptureNum).Monitor(deviceNum).(monitorChannel).Time;
        cursorTime_char = sprintf('%s, ',cursorTime_cell);
        cursorTime_len = length(cursorTime_char);
        cursorTime_char(1:cursorTime_len-2) = [];
        defaultCursorTime = {cursorTime_char};
    
    if ~hasMonitor
        defaultCursorTime = {''};
        monitorChannel_cell = {};
    else
        
    end
    numOfMonitors = length(monitorChannel_cell);

    confirmCursors = false;
    while ~confirmCursors
        fprintf('Enter time values (us) for cursory monitoring...');
        promptCursors = sprintf('Cursor Time (\\mus) {(\\bfUse commas for multiple)}:');
        inputCursorTime = inputdlg( ...
            promptCursors, ...
            'Cursor Time (\mus)', ...
            DIMS, ...
            defaultCursorTime, ...
            opts);
        if isempty(inputCursorTime)
            break;                                 % exit program
        end
        defaultCursorTime = inputCursorTime;
        inputCursorTime_char = inputCursorTime{1};
        cursorTime_use = sprintf( ...
            'Cursor Time (\\mus): \\bf%s',inputCursorTime_char);
        questCursorTime = questdlg( ...
            cursorTime_use, ...
            'Confirm Cursor Time', ...
            BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
            opts);
        % Confirmation
        switch questCursorTime                              % apply choice
            case BUTTON_CONFIRM                         % check confirmation
                confirmCursors = true;               % confirm info
                cursorTime_input = strsplit(inputCursorTime_char,', ');
                cursorTime_num = str2double(cursorTime_input);
                cursorTime_cell = num2cell(transpose(cursorTime_num));
                cursorTime2_cell{deviceNum} = cursorTime_cell;
                File.Data(prevCaptureNum).Monitor(deviceNum).Time = cursorTime_cell;
                fprintf('%s\n',inputCursorTime_char);  % info confirmed
            case BUTTON_TRY                             % try again
                confirmCursors = false;               % trying again
                fprintf('\nTrying agin...\n\n');          % starting over
            case BUTTON_CANCEL                          % quit
                cursorTime_cell = [];
                break;                                 % exit program
            otherwise                                   % cancel
                cursorTime_cell = [];
                break;                                 % exit program
        end
    end

end