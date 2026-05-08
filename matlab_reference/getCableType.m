function [cableType,quitProgram] = getCableType()
%% Constants
% Buttons
BUTTON_PIN = 'Receptacle';
BUTTON_OMNETICS = 'Omnetics';
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
% Titles
TITLE_QUEST_CHECK = 'Check';
% Dimensions
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
confirmCableType = false;       % initialize setup
quitProgram = false;
cableType = [];

%% Function
while confirmCableType == false         	% check setup
    fprintf('Select cable type...');
    % Notice
    prompt1 = 'Select cable type';
    prompt2 = '\bullet 2\times8 Pin Receptacle';
    prompt3 = '\bullet Plexon Omnetics';

    questCableType = questdlg(...
        {prompt1,prompt2,prompt3},...
        'Cable Type',...
        BUTTON_PIN,BUTTON_OMNETICS,BUTTON_CANCEL,...
        opts);
    switch questCableType
        case BUTTON_PIN
            cableType = 0;
        case BUTTON_OMNETICS                  % exit program
            cableType = 1;
        case BUTTON_CANCEL                       % cancel
            fprintf('Quitting...'); % quitting
            quitProgram = 1;
            return;                     % exit program
    end

    % Check
    prompt = sprintf('Selected cable type: {\\bf%s}',questCableType);
    questCableType = questdlg(...                   % question dialog
        prompt,...                    % question prompts
        TITLE_QUEST_CHECK,...                   	% question title
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
        opts);                                      % dialog options
    % Confirmation
    switch questCableType                         	% apply choice
        case BUTTON_CONFIRM                             % check confirmation
            confirmCableType = 1;                  	% confirm parameters
            fprintf('%s.\n',questCableType); % parameters confirmed
        case BUTTON_TRY                     % try again
            fprintf('\nTrying again...\n\n'); % starting over
        case BUTTON_CANCEL                  % quit
            fprintf('\nQuitting...');     % quitting
            quitProgram = 1;
            return;                         % exit program
        otherwise                           % cancel
            fprintf('\nQuitting...');     % quitting
            quitProgram = 1;
            return;                         % exit program
    end
end

end