function [File,isQuit] = getExperimentType(File)
%% Constants
% Buttons
BUTTON_VT = 'Voltage Transient (VT)';
BUTTON_SHORT = 'Short-term Pulsing';
BUTTON_LONG = 'Long-term Pulsing';
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
% Options
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
isQuit = false;
expType = '';

%% Function
opts.Default = BUTTON_VT;       % option dedault
confirmExpType = false;
while ~confirmExpType
    fprintf('Enter stimulation target...');
    questExpType = questdlg('Select stimulation target.', ...
        'Stimulation Target', ...
        BUTTON_VT,BUTTON_SHORT,BUTTON_LONG, ...
        opts);

    promptQuestExpType = sprintf('Stimulation Target: {\\bf%s}',questExpType);
    opts.Default = BUTTON_CONFIRM;
    % Question box
    questConfirmExpType = questdlg(...                   % question dialog
        promptQuestExpType,...                    % question prompts
        'Confirm Stimulation Target',...                     % question title
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
        opts);                                      % dialog options
    % Confirmation
    switch questConfirmExpType                       % apply choice
        case BUTTON_CONFIRM                     % check confirmation
            switch questExpType
                case BUTTON_VT
                    expType = 'VT';
                case BUTTON_SHORT
                    expType = 'SP';
                case BUTTON_LONG
                    expType = 'LP';
            end
            confirmExpType = true;               % confirm parameters
            fprintf('%s\n',questExpType);% parameters confirmed
        case BUTTON_TRY                         % try again
            confirmExpType = false;               % trying again
            fprintf('Trying again...\n');     % starting over
        case BUTTON_CANCEL                      % quit
            fprintf('Quitting...\n\n');         % quitting
            isQuit = true;
            break;                             % exit program
        otherwise                               % cancel
            fprintf('Quitting...\n\n');         % quitting
            isQuit = true;
            breka;                             % exit program
    end
end
if isQuit
    return;
end

%% Store
File.Test.Experiment = expType;

end