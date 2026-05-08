function [File,isQuit] = getIndex2_Tek(File,isAgain)
%% Constants
% Buttons
BUTTON_YES = 'Yes';
BUTTON_QUIT = 'Quit';

%% Variables
isQuit = false;
capture_arr = [File.Data.Index];
captureNum = length(capture_arr);
hasCapture = ~isempty(capture_arr);

%% Function
questCapture = questdlg( ...
    'Do you want to capture oscilloscope data?', ...
    'New Capture', ...
    BUTTON_YES,BUTTON_QUIT,BUTTON_YES);

switch questCapture
    case BUTTON_YES
        if ~isAgain
            if hasCapture
                captureNum = captureNum + 1;
            else
                captureNum = 1;
            end
        else
            captureNum = captureNum - 1;
            if captureNum == 0
                captureNum = 1;
            end
        end
        File.Data(captureNum).Index = captureNum;
    case BUTTON_QUIT
        isQuit = true;
        return;
end

end