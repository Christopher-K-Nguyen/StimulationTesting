function [File,quitProgram] = getIndex_Tek(File)
%% Constants
% Buttons
BUTTON_YES = 'Yes';
BUTTON_QUIT = 'Quit';

%% Variables
quitProgram = false;
capture_arr = [File.Data.Index];
captureNum = length(capture_arr);
hasCapture = ~isempty(captureNum);

%% Function
questCapture = questdlg( ...
    'Do you want to capture oscilloscope data?', ...
    'New Capture', ...
    BUTTON_YES,BUTTON_QUIT,BUTTON_YES);

switch questCapture
    case BUTTON_YES
        if hasCapture
            captureNum = captureNum + 1;
        else
            captureNum = 1;
        end
        File.Data(captureNum).Index = captureNum;
    case BUTTON_QUIT
        quitProgram = true;
end

end