function [...
    amplitude1,...          % first phase amplitude
    phaseWidth1,...         % first phase pulse width
    interphaseDelay,...     % interphase delay
    amplitude2,...          % second phase amplitude
    phaseWidth2,...         % second phase pulse width
    stimRate,...            % stimulation rate
    numOfPulses,...         % number of pulses
	amplitude1_sign,...     % sign of first phase amplitude
    amplitude1_mag,...      % magnitude of second phase amplitude
    amplitude2_sign,...     % sign of first phase amplitude
    amplitude2_mag,...      % magnitude of second phase amplitude
    chargePerPhaseStart,... % starting charge-per-phase
    quitProgram]...         % quit program
    = getAcuteStimParam()   % function
%% Constants
YES = 1;
OPPOSITE = -1;
MICRO_TO_N = 1e-6;
N_TO_NANO = 1e9;
% Buttons
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
DIMS_DIALOG = [1 70];       % dialog dimensions
% Options
opts.Default = BUTTON_CONFIRM;       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
confirmStimParam = 0;
quitProgram = 0;
amplitude1 = 0;
phaseWidth1 = 0;
interphaseDelay = 0;
amplitude2 = 0;
phaseWidth2 = 0;
stimRate = 0;
numOfPulses = 0;
amplitude1_sign = 0;
amplitude1_mag = 0;
amplitude2_sign = 0;
amplitude2_mag = 0;
chargePerPhaseStart = 0;

%% Function
while confirmStimParam == 0
    fprintf('Enter stimulation parameters...');
    % Dialog box
    titleInputStimParam = 'Stimulation'; % input title
    promptInputStimParam = {...                   	% input prompts
        'First phase current amplitude (\muA) {\bf(max \pm1000)}:',...  % first phase amplitude (uA)
        'First phase width (\mus) {\bf(min 5)}:',...              % first phase pulse (us)
        'Interphase delay (\mus):',...                                  % interphase delay (us)
        'Second phase current amplitude (\muA) {\bf(max \pm1000)}:',... % second phase amplitude (uA)
        'Second phase width (\mus) {\bf(min 5)}:',...             % second phase width (us)
        'Stimulation rate (Hz) {\bf(min 0.008, max 50000)}:',...        % stimulation rate (Hz)
        'Number of pulses {\bf(max 32767, 0 for \infty)}:'};            % repetitions

    defaultInputStimParam = {'-20','200','100','20','200','50','0'};    % input defaults
    inputStimParam = inputdlg(...	% input dialog
        promptInputStimParam,...    % input prompts
        titleInputStimParam,...     % input title
        DIMS_DIALOG,...             % dialog dimensions
        defaultInputStimParam,...   % input defaults
        opts);                      % dialog options
    
    % Collect input
    cancelStimParam = isempty(inputStimParam);  % cancel dialog
    if cancelStimParam == YES                   % cancel detected
        fprintf('\nQuitting...');             % quitting
        quitProgram = 1;
        return;                                 % exit program
    end
    amplitude1_cell = inputStimParam{1};        % first phase amplitude (cell)
    amplitude1 = str2double(amplitude1_cell);   % first phase amplitude (double)
    phaseWidth1_cell = inputStimParam{2};       % first phase width (cell)
    phaseWidth1 = str2double(phaseWidth1_cell); % first phase width (double)
    interphaseDelay_cell = inputStimParam{3};           % interphase delay (cell)
    interphaseDelay = str2double(interphaseDelay_cell); % interphase delay (double)
    amplitude2_cell = inputStimParam{4};        % second phase amplitude (cell)
    amplitude2 = str2double(amplitude2_cell);	% second phase amplitude (double)
    phaseWidth2_cell = inputStimParam{5};       % second phase width (cell)
    phaseWidth2 = str2double(phaseWidth2_cell); % second phase width (double)
    stimRate_cell = inputStimParam{6};      % stimulation rate (cell)
    stimRate = str2double(stimRate_cell);   % stimulation rate (double)
    numOfPulses_cell = inputStimParam{7};       % number of pulses (cell)
    numOfPulses = str2double(numOfPulses_cell); % number of pulses (double)    
    
    % Sign and magnitude
    amplitude1_sign = sign(amplitude1);	% sign of first phase amplitude
    amplitude1_mag = abs(amplitude1);   % magnitude of second phase amplitude
    amplitude2_sign = sign(amplitude2);	% sign of first phase amplitude
    amplitude2_mag = abs(amplitude2);   % magnitude of second phase amplitude
    
    % Charge per phase
    amplitude1_mag_A = amplitude1_mag * MICRO_TO_N;
    phaseWidth1_s = phaseWidth1 * MICRO_TO_N;              	% pulse width us to s
    chargePerPhaseStart_C = amplitude1_mag_A * phaseWidth1_s;
    chargePerPhaseStart = chargePerPhaseStart_C * N_TO_NANO; 
    
    % Charge balance
    phase1 = amplitude1_mag * phaseWidth1;
    phase2 = amplitude2_mag * phaseWidth2;
    isOpposite = amplitude1_sign * amplitude2_sign * OPPOSITE;
    if (phase1 == phase2) && (isOpposite == YES)
        isChargeBalanced = 1;
    else
        warning = sprintf('{\\bfWAVEFORM IS NOT CHARGE-BALANCED!}\n');
        isChargeBalanced = 0;
    end

    % Confirm stimulation parameters
    titleQuestStimParam = 'Confirm';
    % Format questions
    chargePerPhaseStart_use = sprintf('{\\bfStarting charge-per-phase = %g nC/ph\n}',chargePerPhaseStart);
    amplitude1_use = sprintf('First phase amplitude: {\\bf%d \\muA}',amplitude1);       % formatted first phase amplitude
    phaseWidth1_use = sprintf('First phase width: {\\bf%d \\mus}',phaseWidth1);   % formatted first phase width
    interphaseDelay_use = sprintf('Interphase delay: {\\bf%d \\mus}',interphaseDelay);  % formatted interphase delay
    amplitude2_use = sprintf('Second phase amplitude: {\\bf%d \\muA}',amplitude2);      % formatted second phase amplitude
    phaseWidth2_use = sprintf('Second phase width: {\\bf%d \\mus}',phaseWidth2);  % formatted second phase width
    stimRate_use = sprintf('Stimulation rate: {\\bf%d Hz}',stimRate);               	% formatted stimulation rate
    if numOfPulses == 0                                                             % formatted repetitions
        numOfPulses_use = sprintf('Number of pulses: {\\bf\\infty pulses}');        % formatted infinite repetitions
    else
        numOfPulses_use = sprintf('Number of pulses: {\\bf%d pulses}',numOfPulses); % formatted finite repetitions
    end
    
    if isChargeBalanced == YES
        promptQuestStimParam = {...	% dialog questions
            chargePerPhaseStart_use,...
            amplitude1_use,...      % inputted first phase amplitude
            phaseWidth1_use,...     % inputted first phase width
            interphaseDelay_use,... % inputted interphase delay
            amplitude2_use,...      % inputted second phase amplitude
            phaseWidth2_use,...     % inputted second phase width
            stimRate_use,...        % inputted stimulation rate
            numOfPulses_use};       % inputted repetitions
    else
        promptQuestStimParam = {...	% dialog questions
            warning,...             % not charge balanced
            chargePerPhaseStart_use,...
            amplitude1_use,...      % inputted first phase amplitude
            phaseWidth1_use,...     % inputted first phase width
            interphaseDelay_use,... % inputted interphase delay
            amplitude2_use,...      % inputted second phase amplitude
            phaseWidth2_use,...     % inputted second phase width
            stimRate_use,...        % inputted stimulation rate
            numOfPulses_use};       % inputted repetitions
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
            fprintf('OK.\n'); % parameters confirmed
        case BUTTON_TRY                     % try again
            fprintf('\nTrying again...\n\n'); % starting over
        case BUTTON_CANCEL                  % quit
            fprintf('\nQuitting...');     % quitting
            quitProgram = 1;
            return;                         % exit program
        otherwise                           % cancel
            fprintf('Quitting...');     % quitting
            quitProgram = 1;
            return;                         % exit program
    end
end
    
end