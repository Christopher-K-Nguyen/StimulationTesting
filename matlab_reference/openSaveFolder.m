function [] = openSaveFolder(savePath)

% Constants
BUTTON_YES = 'Yes';
BUTTON_NO = 'No';
% Options
opts.Default = BUTTON_NO;       % option dedault
opts.Interpreter = 'tex';   % option LaTeX
% Function
titleQuestComplete = 'END';
promptQuestComplete = {...
    'PROGRAM ENDED',...
    'Do you want to open your save folder?'};
questComplete = questdlg(...
    promptQuestComplete,...
    titleQuestComplete,...
    BUTTON_YES,BUTTON_NO,...
    opts);
switch questComplete
    case BUTTON_YES
        winopen(savePath);
    case BUTTON_NO
    otherwise
end

end