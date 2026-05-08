function File = getCursorName2_Tek(File)
%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm';
BUTTON_CANCEL = 'Cancel';
BUTTON_TRY = 'Try Again';
opts.Interpreter = 'tex';   % option LaTeX
DIMS = [1 60];

%% Variables
numOfDevices = length(File.Oscilloscope);
capture_arr = [File.Data.Index];
captureNum = length(capture_arr);
cursorName2_cell = cell(numOfDevices,1);

%% Function
for deviceNum = 1:numOfDevices
    cursorTime_cell = File.Data(captureNum).Monitor(deviceNum).Time;
    numOfCursor = length(cursorTime_cell);
    
    cursorName_cell = cell(numOfCursor,1);
    promptCursorName = cell(1,numOfCursor);
    questCursorName = cell(1,numOfCursor);
    for cursor_idx = 1:numOfCursor
        valueTime = cursorTime_cell{cursor_idx};
        promptCursorName{cursor_idx} = sprintf('%g \\mus',valueTime);
        cursorName_cell{cursor_idx} = num2str(cursor_idx);
    end

    confirmCursorName = false;
    while ~confirmCursorName
        fprintf('Enter label names for cursors...');
        inputCursorName = inputdlg( ...
            promptCursorName, ...
            'Cursor Name', ...
            DIMS, ...
            cursorName_cell, ...
            opts);
        if isempty(inputCursorName)
            break;                                 % exit program
        end

        cursorName_cell(:) = inputCursorName(:);
        for cursor_idx = 1:numOfCursor
            valueTime = cursorTime_cell{cursor_idx};
            valueName = cursorName_cell{cursor_idx};
            questCursorName{cursor_idx} = sprintf('%g \\mus: {\\bf%s}', ...
                valueTime,valueName);
        end
        cursorName_use = sprintf('%s, ',cursorName_cell{:});
        cursorName_len = length(cursorName_use);
        cursorName_use(1:cursorName_len-2);
        questCursorTime = questdlg( ...
            questCursorName, ...
            'Confirm Cursor Name', ...
            BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
            opts);
        % Confirmation
        switch questCursorTime                              % apply choice
            case BUTTON_CONFIRM                         % check confirmation
                confirmCursorName = true;               % confirm info
                cursorName2_cell{deviceNum} = cursorName_cell;
                File.Data(captureNum).Monitor(deviceNum).Name = cursorName_cell;
                fprintf('%s\n',cursorName_use);  % info confirmed
            case BUTTON_TRY                             % try again
                confirmCursorName = false;               % trying again
                fprintf('\nTrying agin...\n\n');          % starting over
            case BUTTON_CANCEL                          % quit
                break;                                 % exit program
            otherwise                                   % cancel
                break;                                 % exit program
        end
    end
end

end