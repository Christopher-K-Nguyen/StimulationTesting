function [startingCycle,longTermCycles,periodicCycle,pauseCycle,quitProgram]...
    = getLongTermTestParam()
%% Constants
YES = 1;
% Buttons
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
DIMS_DIALOG = [1 80];       % dialog dimensions
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
confirmTestParam = 0;
quitProgram = 0;

%% Function
while confirmTestParam == 0
    fprintf('Enter test parameters...');
    
    % Dialog box
    titleInputTestParam = 'Test Parameters';% imput title
    promptInputTestParam = {...           	% input prompts
        'Starting cycle if resuming {\bf(use "e" for scientific notation, otherwise use 0)}',...% starting cycle if resuming
        'Long-term cycles {\bf(use "e" for scientific notation)}:',...                          % long-term cycles
        'Periodic cycle {\bf(use "e" for scientific notation, min 2e5)}:',...                   % periodic cycles
        'Pause cycle {\bf(use "e" for scientific notation, 0 for none)}:'};                     % pause cycle
    defaultInputTestParam = {'0','2e9','1e7','1e8'};% input defaults
    inputTestParam = inputdlg(...   % input dialog
        promptInputTestParam,...    % input prompts
        titleInputTestParam,...     % input title
        DIMS_DIALOG,...             % dialog dimensions
        defaultInputTestParam,...   % input defaults
        opts);                      % dialog options
        
    % Collect input
    startingCycle_cell = inputTestParam{1};             % starting cycle (cell)
    startingCycle = str2double(startingCycle_cell);     % starting cycle (double)
    longTermCycles_cell = inputTestParam{2};            % long-term cycles (cell)
    longTermCycles = str2double(longTermCycles_cell);   % long-term cycles (double)
    periodicCycle_cell = inputTestParam{3};             % periodic cycle (cell)
    periodicCycle = str2double(periodicCycle_cell);     % periodic cycle (double)
    pauseCycle_cell = inputTestParam{4};                % pause cycle (cell)
    pauseCycle = str2double(pauseCycle_cell);           % pause recording (double)
    cancelTestParam = isempty(inputTestParam);  % cancel dialog
    if cancelTestParam == YES                 	% cancel detected
        fprintf('\nQuitting...');               % quitting
        quitProgram = 1;
        return;                                 % exit program
    end

    % Confirm test parameters
    titleQuestTestParam = 'Confirm Test Parameters';
    % Format questions
    startingCycle_use = sprintf('Starting cycle: {\\bf%g cycles}',startingCycle);       % formatted starting cycle
    longTermCycles_use = sprintf('Long-term cycles: {\\bf%g cycles}',longTermCycles);   % formatted long-term cycles
    periodicCycle_use = sprintf('Periodic cycle: {\\bf%.g cycles}',periodicCycle);       % formatted periodic cycle
    pauseCycle_use = sprintf('Pause cycle: {\\bf%.g cycles}',pauseCycle);                % formatted pause cycle
    % Prompt
    promptQuestTestParam = {... % question prompts
        startingCycle_use,...   % inputted starting cycle
        longTermCycles_use,...  % inputted long-term cycles
        periodicCycle_use,...   % inputted periodic cycle
        pauseCycle_use};        % inputted pause cycle
    % Question box
    questTestParam = questdlg(...                   % question dialog
        promptQuestTestParam,...                    % question prompts
        titleQuestTestParam,...                     % question title
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
        opts);                                      % dialog options
    % Confirmation
    switch questTestParam                       % apply choice
        case BUTTON_CONFIRM                     % check confirmation
            confirmTestParam = 1;               % confirm parameters
            fprintf('OK.\n\n');% parameters confirmed
        case BUTTON_TRY                         % try again
            confirmTestParam = 0;               % trying again
            fprintf('\nTrying again...\n\n');     % starting over
        case BUTTON_CANCEL                      % quit
            fprintf('\nQuitting...');         % quitting
            quitProgram = 1;
            return;                             % exit program
        otherwise                               % cancel
            fprintf('\nQuitting...');         % quitting
            quitProgram = 1;
            return;                             % exit program
    end
end

end