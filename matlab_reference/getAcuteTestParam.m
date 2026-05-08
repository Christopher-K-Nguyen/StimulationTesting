function [lowerPotential,upperPotential,chargePhaseLimit,stepSize,quitProgram]...
    = getAcuteTestParam(chargePhaseStart)
%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
DIMS_DIALOG = [1 75];       % dialog dimensions
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
chargePhaseStart_str = sprintf('%g',chargePhaseStart);
confirmTestParam = 0;
quitProgram = 0;
lowerPotential = 0;
upperPotential = 0;
chargePhaseLimit = 0;
stepSize = 0;

%% Function
while confirmTestParam == 0
    fprintf('Enter test parameters...');
    
    % Dialog box
    titleInputTestParam = 'Test Parameters';% imput title
    promptInputTestParam = {...           	% input prompts
        'Cathodic potential limit (V) {\bf({\itE}_{Pt} = {\itE}_{Ag|AgCl} - 0.3)}:',... % lower potential limit (V)
        'Anodic potential limit (V) {\bf({\itE}_{Pt} = {\itE}_{Ag|AgCl} - 0.3)}:',...   % upper potential limit (V)
        'Target charge-per-phase (nC/ph) {\bf(0 for {\itQ}_{max})}:',...     % current limit (uA)
        'Step size (\muA) {\bf(min 0.03, not used for {\itQ}_{max})}:'};     % step size (uA)
    defaultInputTestParam = {'-0.9','0.5',chargePhaseStart_str,'0'};  	% input defaults
    inputTestParam = inputdlg(...   % input dialog
        promptInputTestParam,...    % input prompts
        titleInputTestParam,...     % input title
        DIMS_DIALOG,...             % dialog dimensions
        defaultInputTestParam,...   % input defaults
        opts);                      % dialog options
        
    % Collect input
    cancelTestParam = isempty(inputTestParam);  % cancel dialog
    if cancelTestParam == 1                     % cancel detected
        fprintf('\nQuitting...');             % quitting
        quitProgram = 1;
        return;                                 % exit program
    end
    lowerPotential_cell = inputTestParam{1};                % lower potential (cell)
    lowerPotential = str2double(lowerPotential_cell);       % lower potential (double)
    upperPotential_cell = inputTestParam{2};                % upper potential (cell)
    upperPotential = str2double(upperPotential_cell);       % upper potential (double)
    chargePhaseLim_cell = inputTestParam{3};             % current limit (cell)
    chargePhaseLimit = str2double(chargePhaseLim_cell); % current limit (double)
    stepSize_cell = inputTestParam{4};      % step size (cell)
    stepSize = str2double(stepSize_cell);   % step size (double)

    % Confirm test parameters
%     fprintf('Confirm test parameters...');
    titleQuestTestParam = 'Confirm Test Parameters';
    % Format questions
    lowerPotential_use = sprintf('Cathodic potential limit: {\\bf%.1f V}',lowerPotential);  % formatted lower potential
    upperPotential_use = sprintf('Anodic potential limit: {\\bf%.1f V}',upperPotential);	% formatted upper potential
    stepSize_use = sprintf('Step size: {\\bf%.2f \\muA}',stepSize);                                 % formatted step size
    if chargePhaseLimit == 0                                                                    	% formatted charge per phase
        chargePhaseLimit = Inf;
        chargePhaseLim_use = sprintf('Target charge-per-phase: {\\bf{\\itQ}_{max}}');                    % formatted max cathodal
        promptQuestTestParam = {... % question prompts
            lowerPotential_use,...  % inputted lower potential
            upperPotential_use,...  % inputted upper potential
            chargePhaseLim_use};    % inputted current limit
    else
        chargePhaseLim_use = sprintf('Target charge-per-phase: {\\bf%g nC/ph}',chargePhaseLimit);    % formatted charge
        promptQuestTestParam = {...     % question prompts
            lowerPotential_use,...      % inputted lower potential
            upperPotential_use,...      % inputted upper potential
            chargePhaseLim_use,...   % inputted current limit
            stepSize_use};              % inputted step size
    end

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
            fprintf('OK.\n');% parameters confirmed
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