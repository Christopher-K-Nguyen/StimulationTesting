function [File,quitProgram] = selectExperiments(File)

%% Constants
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
% Options
opts.Default = BUTTON_CONFIRM;       % option dedault
opts.Interpreter = 'tex';   % option LaTeX
% Choices
VT_INITIAL = 'Prior Voltage Transients';
PULSING = 'Fixed Pulsing';
VT_FINAL = 'Post Voltage Transients';

%% Variables
exp_cell = {VT_INITIAL,PULSING,VT_FINAL};
numOfExp = length(exp_cell);
initialChoice = 1:numOfExp;
confirmExpType = false;

%% Function

% Subject Select
while ~confirmExpType
    fprintf('Enter experiment selections...');
    % Confirm subject selection
    % Dialog box
    [deviceType_idx,deviceType_tf] = listdlg(...	% list dialog
        'PromptString','Select device type',... % list prompts
        'ListString',exp_cell,...    % list
        'Name','Device Type Selection',...
        'InitialValue',initialChoice,...
        'SelectionMode','multiple');

    % Collect input
    if ~deviceType_tf           % cancel detected
        fprintf('\nQuitting...'); % quitting
        quitProgram = true;
        break;                     % exit program
    else
        initialChoice = deviceType_idx;
    end
    expChoice = exp_cell{deviceType_idx};

    % Confirm subject selection
    % Format questions
    promptQuestDeviceType = sprintf('Experiment type selected: {\\bf%s}',expChoice);
    % Question box
    questListSubject = questdlg(...                      % question dialog
        promptQuestDeviceType,...                       % question prompts
        'Confirm Experiment Type Selection',...                        % question title
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
        opts);                                      % dialog options
    % Confirmation
    switch questListSubject                              % apply choice
        case BUTTON_CONFIRM                         % check confirmation
            confirmExpType = true;               % confirm info
            switch expChoice
                case UTD
                    deviceType = 'UTD';
                case BRN
                    deviceType = 'BRN';
                case NNX
                    deviceType = 'NNX';
                case PLX
                    deviceType = 'PLX';
            end
            fprintf('%s\n',expChoice);  % info confirmed
        case BUTTON_TRY                             % try again
            confirmExpType = false;               % trying again
            fprintf('\nTrying agin...\n\n');          % starting over
        case BUTTON_CANCEL                          % quit
            fprintf('\nQuitting...');             % quitting
            quitProgram = true;
            break;                                 % exit program
        otherwise                                   % cancel
            fprintf('\nQuitting...');             % quitting
            quitProgram = true;
            break;                                 % exit program
    end
end
if quitProgram
    return;
end

end