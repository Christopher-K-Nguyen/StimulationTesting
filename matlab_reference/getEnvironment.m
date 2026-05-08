function [File,isQuit] = getEnvironment(File)
%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
BUTTON_SALINE = 'Electrolyte';
BUTTON_ANIMAL = 'Animal';
% Options
opts.Default = BUTTON_CONFIRM;       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
expType = File.Test.Experiment;
isVT = contains2(expType,'VT');
confirmEnvironment = false;
isQuit = false;

%% Function
if isVT
    while ~confirmEnvironment
        fprintf('Enter experiment environment...');
        % Format questions
        promptQuestInput = {'Select experiment environment.'};
        % Question box
        questEnvironment = questdlg(...                      % question dialog
            promptQuestInput,...                       % question prompts
            'Experiment Environment',...                        % question title
            BUTTON_SALINE,BUTTON_ANIMAL,BUTTON_SALINE); % buttons

        % Format questions
        promptQuestEnvironment = sprintf( ...
            'Select experiment environment: \\bf{%s}', ...
            questEnvironment);
        % Question box
        questConfirmEnvironment = questdlg(...                      % question dialog
            promptQuestEnvironment,...                       % question prompts
            'Experiment Environment',...                        % question title
            BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
            opts);                                      % dialog options
        % Confirmation
        switch questConfirmEnvironment                              % apply choice
            case BUTTON_CONFIRM                         % check confirmation
                confirmEnvironment = true;               % confirm info
                environment = questEnvironment;
                switch questEnvironment
                    case BUTTON_SALINE
                        tag = 'electrolyte';
                    case BUTTON_ANIMAL
                        tag = 'animal';
                end
                fprintf('%s\n',tag);  % info confirmed
            case BUTTON_TRY                             % try again
                confirmEnvironment = false;               % trying again
                fprintf('\nTrying agin...\n\n');          % starting over
            case BUTTON_CANCEL                          % quit
                fprintf('\nQuitting...');             % quitting
                isQuit = true;
                break;                                 % exit program
            otherwise                                   % cancel
                fprintf('\nQuitting...');             % quitting
                isQuit = true;
                break;                                 % exit program
        end
    end
    if isQuit
        return;
    end
else
    tag = 'electrolyte';
end

%% Store
File.Parameters.Environment = tag;

end