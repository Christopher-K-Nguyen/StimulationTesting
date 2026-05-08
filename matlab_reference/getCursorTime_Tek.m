function cursorTime_arr = getCursorTime_Tek(File)
%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm';
BUTTON_CANCEL = 'Cancel';
BUTTON_TRY = 'Try Again';
opts.Default = BUTTON_CONFIRM;
opts.Interpreter = 'tex';   % option LaTeX
DIMS = [1 70];

%% Variables
% Monitor
if captureNum > 1
    prevCaptureNum = captureNum - 1;
else
    prevCaptureNum = 1;
end
Monitor = File.Data(prevCaptureNum).Monitor;
hasMonitor = isstruct(Monitor);
if ~hasMonitor
    defaultCursorTime = {''};
else
    cursorTime_arr = File.Data(prevCaptureNum).Monitor.Cursor.Time;
    cursorTime_char = sprintf('%s, ',cursorTime_arr);
    cursorTime_len = length(cursorTime_char);
    cursorTime_char(1:cursorTime_len-2) = [];
    defaultCursorTime = {cursorTime_char};
end
confirmCursors = false;

%% Function
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
            cursorTime_cell = strsplit(inputCursorTime_char,', ');
            cursorTime_num = str2double(cursorTime_cell);
            cursorTime_arr = transpose(cursorTime_num);
            fprintf('%s\n',inputCursorTime_char);  % info confirmed
        case BUTTON_TRY                             % try again
            confirmCursors = false;               % trying again
            fprintf('\nTrying agin...\n\n');          % starting over
        case BUTTON_CANCEL                          % quit
            cursorTime_arr = [];
            break;                                 % exit program
        otherwise                                   % cancel
            cursorTime_arr = [];
            break;                                 % exit program
    end
end

end