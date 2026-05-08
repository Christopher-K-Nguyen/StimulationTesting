function [File,isQuit] = getStimParam3(File)
    % [...
    % amplitude1,...          % first phase amplitude
    % phaseWidth1,...         % first phase pulse width
    % interphaseDelay,...     % interphase delay
    % amplitude2,...          % second phase amplitude
    % phaseWidth2,...         % second phase pulse width
    % stimRate,...            % stimulation rate
    % numOfPulses,...         % number of pulses
	% amplitude1_sign,...     % sign of first phase amplitude
    % amplitude1_mag,...      % magnitude of second phase amplitude
    % amplitude2_sign,...     % sign of first phase amplitude
    % amplitude2_mag,...      % magnitude of second phase amplitude
    % chargePerPhaseStart,... % starting charge-per-phase
    % quitProgram]...         % quit program
%% Constants
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
confirmStimParam = false;
isQuit = false;
amplitude1 = 0;
phaseWidth1 = 0;
interphaseDelay = 0;
amplitude2 = 0;
phaseWidth2 = 0;
stimRate = 0;
numOfPulses = 0;
amplitude1_sign = 0;
amplitude1_cell = '-20';        % first phase amplitude (cell)
phaseWidth1_cell = '200';       % first phase width (cell)
interphaseDelay_cell = '100';           % interphase delay (cell)
amplitude2_cell = '20';        % second phase amplitude (cell)
phaseWidth2_cell = '200';       % second phase width (cell)
stimRate_cell = '50';      % stimulation rate (cell)
numOfPulses_cell = '0';       % number of pulses (cell)

%% Function
while ~confirmStimParam
    fprintf('Enter stimulation parameters...');
    % Dialog box
    titleInputStimParam = 'Stimulation'; % input title
    promptInputStimParam = {...                   	% input prompts
        'First phase current amplitude (\muA) {\bf(max \pm1,000)}:',...  % first phase amplitude (uA)
        'First phase width (\mus) {\bf(min 5)}:',...              % first phase pulse (us)
        'Interphase delay (\mus):',...                                  % interphase delay (us)
        'Second phase current amplitude (\muA) {\bf(max \pm1,000)}:',... % second phase amplitude (uA)
        'Second phase width (\mus) {\bf(min 5)}:',...             % second phase width (us)
        'Stimulation rate (pps or Hz) {\bf(min 0.008, max 100,000)}:',...        % stimulation rate (Hz)
        'Number of pulses {\bf(max 32767, 0 for \infty)}:'};            % repetitions
    defaultInputStimParam = { ...
        amplitude1_cell,phaseWidth1_cell, ...
        interphaseDelay_cell, ...
        amplitude2_cell,phaseWidth2_cell, ...
        stimRate_cell,numOfPulses_cell};    % input defaults
    inputStimParam = inputdlg(...	% input dialog
        promptInputStimParam,...    % input prompts
        titleInputStimParam,...     % input title
        DIMS_DIALOG,...             % dialog dimensions
        defaultInputStimParam,...   % input defaults
        opts);                      % dialog options
    
    % Collect input
    cancelStimParam = isempty(inputStimParam);  % cancel dialog
    if cancelStimParam                   % cancel detected
        fprintf('\nQuitting...');             % quitting
        isQuit = true;
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
    if isempty(numOfPulses_cell) || contains2(numOfPulses_cell,{'0','inf','infty','infinity'})
        numOfPulses = Inf;
    else
        numOfPulses = str2double(numOfPulses_cell); % number of pulses (double) 
    end
       
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
    if (phase1 == phase2) && (amplitude1_sign == amplitude2_sign * -1)
        isChargeBalance = true;
    else
        isChargeBalance = false;
    end

    % Symmetry
    isSymmetric = (amplitude1_mag == amplitude2_mag) && (phaseWidth1 == phaseWidth2);

    % Overlap
    pulseWidth = (phaseWidth1 + interphaseDelay + phaseWidth2) * MICRO_TO_N;
    stimPeriod = 1 / stimRate;
    isOverlap = pulseWidth >= stimPeriod;

    % Check waveform
    if ~isChargeBalance && isOverlap
        isBad = true;
        warning = sprintf('{\\bfWAVEFORM IS OVERLAPPING & NOT CHARGE-BALANCED!}\n');
    elseif ~isChargeBalance
        isBad = true;
        warning = sprintf('{\\bfWAVEFORM IS NOT CHARGE-BALANCED!}\n');
    elseif isOverlap
        isBad = true;
        warning = sprintf('{\\bfWAVEFORM IS OVERLAPPING!}\n');
    else
        isBad = false;
        warning = '';
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
    
    if ~isBad
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
            fprintf('OK\n'); % parameters confirmed
        case BUTTON_TRY                     % try again
            fprintf('\nTrying again...\n\n'); % starting over
        case BUTTON_CANCEL                  % quit
            fprintf('\nQuitting...');     % quitting
            isQuit = true;
            return;                         % exit program
        otherwise                           % cancel
            fprintf('Quitting...');     % quitting
            isQuit = true;
            break;                         % exit program
    end
end
if isQuit
    return;
end
    
%% Store
File.Parameters.Amplitude1 = amplitude1;
File.Parameters.PhaseWidth1 = phaseWidth1;
File.Parameters.InterphaseDelay = interphaseDelay;
File.Parameters.Amplitude2 = amplitude2;
File.Parameters.PhaseWidth2 = phaseWidth2;
File.Parameters.StimulationRate = stimRate;
File.Parameters.NumberOfPulses = numOfPulses;
File.Parameters.Polarity = amplitude1_sign;
File.Parameters.Symmetry = isSymmetric;

end