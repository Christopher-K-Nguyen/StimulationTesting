function [confirmCapture,isSave,isAgain] = getSave_Tek()
%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm';
BUTTON_TRY = 'Try Again';
BUTTON_NEXT = 'Next';
BUTTON_QUIT = 'Quit';
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
confirmCapture = false;
isSave = false;
isAgain = false;

%% Function
confirmChoice = false;
while ~confirmChoice
    opts.Default = [];
    questData = questdlg( ...
        'Do you want to keep and save?', ...
        'Keep Data', ...
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_NEXT, ...
        opts);
    switch questData
        case BUTTON_CONFIRM
            confirmCapture = true;
            isSave = true;
            isAgain = false;
            msg = 'Keeping and saving data.';
        case BUTTON_TRY
            confirmCapture = false;
            isSave = false;
            isAgain = true;
            msg = 'Trying again.';
        case BUTTON_NEXT
            confirmCapture = true;
            isSave = false;
            isAgain = false;
            msg = 'Moving on.';
    end

    questPrompt = sprintf('Selected: {\\bf%s}\n%s',questData,msg);
    opts.Default = BUTTON_CONFIRM;
    questInfo = questdlg( ...
        questPrompt, ...
        'Selection', ...
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_QUIT, ...
        opts);
    switch questInfo
        case BUTTON_CONFIRM
            fprintf('%s\n\n',msg);
            confirmChoice = true;
        case BUTTON_TRY
            continue;
        case BUTTON_QUIT
            confirmCapture = false;
            isSave = false;
            isAgain = false;
            return;
    end

end