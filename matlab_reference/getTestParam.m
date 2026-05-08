function [lowerPotential,upperPotential,chargePerPhaseLimit,stepSize,quitProgram]...
    = getTestParam()
%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
DIMS_DIALOG = [1 60];       % dialog dimensions
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
confirmTestParam = 0;
quitProgram = 0;

%% Function
while confirmTestParam == 0
    disp('Enter test parameters...');
    
    % Dialog box
    titleInputTestParam = 'Test Parameters';% imput title
    promptInputTestParam = {...           	% input prompts
        'Lower (cathodal) potential limit (V):',...             % lower potential limit (V)
        'Upper (anodal) potential limit (V):',...               % upper potential limit (V)
        'Charge per phase (nC/ph) {\bf(0 for {\itE}_{mc})}:',...            % current limit (uA)
        'Step size (\muA) {\bf(min 0.03, not used for {\itE}_{mc})}:'};  % step size (uA)
    defaultInputTestParam = {'-0.6','0.8','4','0.5'};           % input defaults
    inputTestParam = inputdlg(...   % input dialog
        promptInputTestParam,...    % input prompts
        titleInputTestParam,...     % input title
        DIMS_DIALOG,...             % dialog dimensions
        defaultInputTestParam,...   % input defaults
        opts);                      % dialog options
        
    % Collect input
    lowerPotential_cell = inputTestParam{1};                % lower potential (cell)
    lowerPotential = str2double(lowerPotential_cell);       % lower potential (double)
    upperPotential_cell = inputTestParam{2};                % upper potential (cell)
    upperPotential = str2double(upperPotential_cell);       % upper potential (double)
    chargePerPhaseLim_cell = inputTestParam{3};             % current limit (cell)
    chargePerPhaseLimit = str2double(chargePerPhaseLim_cell); % current limit (double)
    stepSize_cell = inputTestParam{4};      % setp size (cell)
    stepSize = str2double(stepSize_cell);   % step size (double)
    cancelTestParam = isempty(inputTestParam);  % cancel dialog
    if cancelTestParam == 1                     % cancel detected
        fprintf('Quitting...\n\n');             % quitting
        quitProgram = 1;
        return;                                 % exit program
    end

    % Confirm test parameters
    titleQuestTestParam = 'Confirm Stimulation Parameters';
    % Format questions
    lowerPotential_use = sprintf('Lower (cathodal) potential limit: {\\bf%.1f V}',lowerPotential);  % formatted lower potential
    upperPotential_use = sprintf('Upper (anodal) potential limit: {\\bf%.1f V}',upperPotential);	% formatted upper potential
    if chargePerPhaseLimit == 0                                                                    	% formatted charge per phase
        chargePerPhaseLim_use = sprintf('Charge per phase: {\\bf{\\itE}_{mc}}');                    % formatted max cathodal
    else
        opts.Interpreter = 'tex';   % option LaTeX
        chargePerPhaseLim_use = sprintf('Charge per phase: {\\bf%d nC/ph}',chargePerPhaseLimit);      % formatted charge

    end
    stepSize_use = sprintf('Step size: {\\bf%.2f \\muA}',stepSize);                                 % formatted step size
    promptQuestTestParam = {...     % question prompts
        lowerPotential_use,...      % inputted lower potential
        upperPotential_use,...      % inputted upper potential
        chargePerPhaseLim_use,...   % inputted current limit
        stepSize_use};              % inputted step size
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
            fprintf('Test parameters set.\n\n');% parameters confirmed
        case BUTTON_TRY                         % try again
            confirmTestParam = 0;               % trying again
            fprintf('Trying again...\n\n');     % starting over
        case BUTTON_CANCEL                      % quit
            fprintf('Quitting...\n\n');         % quitting
            quitProgram = 1;
            return;                             % exit program
        otherwise                               % cancel
            fprintf('Quitting...\n\n');         % quitting
            quitProgram = 1;
            return;                             % exit program
    end
end

end