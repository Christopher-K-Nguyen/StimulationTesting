function [File,isQuit] = getExperimentTest(File)

%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
% Options
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
isQuit = false;
expType = '';

%% Experiment Test
expTest_cell = {'Biphasic Voltage Transient','Triphasic Voltage Transient','Short-term Pulsing','Long-term Pulsing','Progressive-Stress'};
expID_cell = {'VT','TV','SP','LP','PS'};
confirmExpType = false;
while ~confirmExpType
    fprintf('Enter stimulation test...');
    [list_idx,~] = listdlg( ...
        'ListString',expTest_cell, ...
        'PromptString','Select stimulation test type.', ...
        'SelectionMode','single', ...
        'ListSize',[200 300], ...
        'InitialValue',1);
    questExpType = expTest_cell{list_idx};
    promptQuestExpType = sprintf('Stimulation Test: {\\bf%s}',questExpType);
    opts.Default = BUTTON_CONFIRM;
    % Question box
    questConfirmExpType = questdlg(...                   % question dialog
        promptQuestExpType,...                    % question prompts
        'Confirm Stimulation test',...                     % question title
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
        opts);                                      % dialog options
    % Confirmation
    switch questConfirmExpType                       % apply choice
        case BUTTON_CONFIRM                     % check confirmation
            expType = expID_cell{list_idx};
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