function [...
    amplitude1,...      % first phase amplitude
    pulseWidth1,...     % first phase pulse width
    interphaseDelay,... % interphase delay
    amplitude2,...      % second phase amplitude
    pulseWidth2,...     % second phase pulse width
    stimRate,...        % stimulation rate
    amplitude1_sign,... % sign of first phase
    amplitude1_mag,...  % magnitude of first amplitude
    quitProgram]...
    = getLongTermStimParam()
%% Constants
YES = 1;
% Buttons
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
DIMS_DIALOG = [1 70];       % dialog dimensions
% Options
opts.Default = 'yes';       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
confirmStimParam = 0;
quitProgram = 0;

%% Function
while confirmStimParam == 0
    fprintf('Enter stimulation parameters...');
    % Dialog box
    titleInputStimParam = 'Stimulation Parameters'; % input title
    promptInputStimParam = {...                   	% input prompts
        'First phase current amplitude (\muA) {\bf(max \pm1000)}:',...  % first phase amplitude (uA)
        'First phase pulse width (\mus) {\bf(min 5)}:',...              % first phase pulse (us)
        'Interphase delay (\mus):',...                                  % interphase delay (us)
        'Second phase current amplitude (\muA) {\bf(max \pm1000)}:',... % second phase amplitude (uA)
        'Second phase pulse width (\mus) {\bf(min 5)}:',...             % second phase width (us)
        'Stimulation rate (Hz) {\bf(min 0.008, max 50000)}:'};          % stimulation rate (Hz)

    defaultInputStimParam = {'-40','200','100','40','200','200'};       % input defaults
    inputStimParam = inputdlg(...	% input dialog
        promptInputStimParam,...    % input prompts
        titleInputStimParam,...     % input title
        DIMS_DIALOG,...             % dialog dimensions
        defaultInputStimParam,...   % input defaults
        opts);                      % dialog options
    cancelStimParam = isempty(inputStimParam);  % cancel dialog

    % Collect input
    amplitude1_cell = inputStimParam{1};        % first phase amplitude (cell)
    amplitude1 = str2double(amplitude1_cell);   % first phase amplitude (double)
    pulseWidth1_cell = inputStimParam{2};       % first phase width (cell)
    pulseWidth1 = str2double(pulseWidth1_cell); % first phase width (double)
    interphaseDelay_cell = inputStimParam{3};           % interphase delay (cell)
    interphaseDelay = str2double(interphaseDelay_cell); % interphase delay (double)
    amplitude2_cell = inputStimParam{4};        % second phase amplitude (cell)
    amplitude2 = str2double(amplitude2_cell);	% second phase amplitude (double)
    pulseWidth2_cell = inputStimParam{5};       % second phase width (cell)
    pulseWidth2 = str2double(pulseWidth2_cell); % second phase width (double)
    stimRate_cell = inputStimParam{6};      % stimulation rate (cell)
    stimRate = str2double(stimRate_cell);   % stimulation rate (double)
    if cancelStimParam == 1                     % cancel detected
        fprintf('\nQuitting...');             % quitting
        quitProgram = 1;
        return;                                 % exit program
    end

    % Sign of first phase
    amplitude1_sign = sign(amplitude1);	% sign of first phase amplitude
    amplitude1_mag = abs(amplitude1);    % magnitude of first phase amplitude

    phase1 = abs(amplitude1) * pulseWidth1;
    phase2 = abs(amplitude2) * pulseWidth2;
    isOpposite = sign(amplitude1) * sign(amplitude2) * amplitude1_sign;
    if (phase1 == phase2) && (isOpposite == YES)
        isChargeBalanced = 1;
    else
        warning = sprintf('{\\bfWAVEFORM IS NOT CHARGE-BALANCED!}');
        isChargeBalanced = 0;
    end

    % Confirm stimulation parameters
    titleQuestStimParam = 'Confirm';
    % Format questions
    amplitude1_use = sprintf('First phase amplitude: {\\bf%d \\muA}',amplitude1);       % formatted first phase amplitude
    pulseWidth1_use = sprintf('First phase pulse width: {\\bf%d \\mus}',pulseWidth1);   % formatted first phase width
    interphaseDelay_use = sprintf('Interphase delay: {\\bf%d \\mus}',interphaseDelay);  % formatted interphase delay
    amplitude2_use = sprintf('Second phase amplitude: {\\bf%d \\muA}',amplitude2);      % formatted second phase amplitude
    pulseWidth2_use = sprintf('Second phase pulse width: {\\bf%d \\mus}',pulseWidth2);  % formatted second phase width
    stimRate_use = sprintf('Stimulation rate: {\\bf%d Hz}',stimRate);               	% formatted stimulation rate
    
    if isChargeBalanced == YES
        promptQuestStimParam = {...	% dialog questions
            amplitude1_use,...      % inputted first phase amplitude
            pulseWidth1_use,...     % inputted first phase width
            interphaseDelay_use,... % inputted interphase delay
            amplitude2_use,...      % inputted second phase amplitude
            pulseWidth2_use,...     % inputted second phase width
            stimRate_use};          % inputted stimulation rate
    else
        promptQuestStimParam = {...	% dialog questions
            warning,...             % not charge balanced
            amplitude1_use,...      % inputted first phase amplitude
            pulseWidth1_use,...     % inputted first phase width
            interphaseDelay_use,... % inputted interphase delay
            amplitude2_use,...      % inputted second phase amplitude
            pulseWidth2_use,...     % inputted second phase width
            stimRate_use};          % inputted stimulation rate
    end

    % Question box
    questStimParam = questdlg(...                   % question dialog
        promptQuestStimParam,...                    % question prompts
        titleQuestStimParam,...                   	% question title
        BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
        opts);                                      % dialog options
    % Confirmation
    switch questStimParam                               % apply choice
        case BUTTON_CONFIRM                             % check confirmation
            confirmStimParam = 1;                       % confirm parameters
            fprintf('OK..\n\n'); % parameters confirmed
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