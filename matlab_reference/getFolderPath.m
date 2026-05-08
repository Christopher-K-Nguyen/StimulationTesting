function [folderPath,isQuit] = getFolderPath()
%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
% Options
opts.Default = BUTTON_CONFIRM;       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
confirmFolderPath = false;
isQuit = false;

%% Function
while ~confirmFolderPath
    fprintf('Select folder path...');
    
    % Input save path
    titleMsgFolderPath = 'Folder Path';                             % message title
    promptMsgtFolderPath = 'Press OK to select folder.';   % message prompt
    waitfor(msgbox(promptMsgtFolderPath,titleMsgFolderPath));       % message box
    folderPath = uigetdir('','Folder Folder');                      % input folder folder
    if isempty(folderPath) || ~ischar(folderPath)                      % cancel detected
        fprintf('\nQuitting...');         % quitting
        isQuit = true;
        break;                             % exit program
    end

    % Confirm folder path
    titleQuestFolderPath = 'Confirm Folder Path';   % question title
    promptQuestFolderPath1 = 'Folder path:';        % message
    promptQuestFolderPath2 = folderPath;            % inputted folder path
    
    % Question box
    questFolderPath = questdlg(...                    % question dialog
        {promptQuestFolderPath1,...                   % question prompt 1
        promptQuestFolderPath2},...                   % question prompt 2
        titleQuestFolderPath,...                      % question title
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
        opts);                                      % dialog options
    
    % Confirmation
    switch questFolderPath                    % apply choice
        case BUTTON_CONFIRM                 % check confirmation
            confirmFolderPath = true;            % confirm path
            fprintf('OK\n\n');             % path confirmed
        case BUTTON_TRY                     % try again
            confirmFolderPath = false;            % trying again
            fprintf('\nTrying again...\n\n'); % starting over
        case BUTTON_CANCEL                  % quit
            fprintf('\nQuitting...');     % quitting
            isQuit = true;
            break;                         % exit program
        otherwise                           % cancel
            fprintf('\nQuitting...\');     % quitting
            isQuit = true;
            break;                         % exit program
    end
end
if isQuit
    return;
end

end