function [File,isQuit] = getParameters(File)
%% Constants
% Buttons
BUTTON_CONFIRM = 'Confirm'; % confirm button
BUTTON_TRY = 'Start Over';  % start over button
BUTTON_CANCEL = 'Cancel';   % cancel button
BUTTON_YES = 'Yes';
BUTTON_NO = 'No';
BUTTON_TIME = 'Time';
BUTTON_DIFF = 'Difference';
DIMS_DIALOG = [1 100];       % dialog dimensions
% Options
opts.Default = BUTTON_CONFIRM;       % option dedault
opts.Interpreter = 'tex';   % option LaTeX

%% Variables
isQuit = false;

% Experiment
expType = File.Test.Experiment;
isPulsing = contains2(expType,{'SP','LP'});
isLongPulsing = contains2(expType,'LP');
isTriphasic = contains2(expType,'TV');

% Area
surfaceArea_arr = File.Parameters.SurfaceArea;
surfaceArea = min(surfaceArea_arr);

% Groups
channelGroup_mat = File.Test.Groups;
[numOfGroups,~] = size(channelGroup_mat);

% Parameter Initialization
amplitude1 = 0;
phaseWidth1 = 0;
interphaseDelay = 0;
amplitude2 = 0;
phaseWidth2 = 0;
stimRate = 0;
numOfPulses = 0;
amplitude_sign = 0;
if isTriphasic
    defaultAmplitude1 = '10';        % first phase amplitude (cell)
    defaultPhaseWidth1 = '50';       % first phase width (cell)
    defaultAmplitude2 = '-15';        % second phase amplitude (cell)
    defaultPhaseWidth2 = '50';       % second phase width (cell)
    defaultAmplitude3 = '5';        % second phase amplitude (cell)
    defaultPhaseWidth3 = '50';       % second phase width (cell)
    defaultInterphaseDelay = '20';
    defaultDischargeDelay = '20';       % second phase width (cell)
    defaultNumOfPulses = 'Inf';
else
    defaultAmplitude1 = '-10';        % first phase amplitude (cell)
    defaultPhaseWidth1 = '200';       % first phase width (cell)
    defaultInterphaseDelay = '100';           % interphase delay (cell)
    defaultAmplitude2 = '10';        % second phase amplitude (cell)
    defaultPhaseWidth2 = '200';       % second phase width (cell)
    defaultDischargeDelay = '200';       % second phase width (cell)
    defaultNumOfPulses = 'Inf';       % number of pulses (cell)
end
if isPulsing
    if isLongPulsing
        defaultNumOfPulses = '1e7'; % number of pulses (cell)
        defaultPeriodic = '1e6';
    else
        defaultNumOfPulses = '1e5'; % number of pulses (cell)
    end
end
defaultStimRate = '50';      % stimulation rate (cell)
defaultBias = '0';

%% Function
confirmStimParam = false;
while ~confirmStimParam
    fprintf('Enter stimulation parameters...');
    % Dialog box
    titleInputStimParam = 'Stimulation'; % input title
    if isPulsing
        numOfPulses_quest = 'Number of pulses for pulsing {\bf(may use "e" or "E" for scientific notation)}:';
        periodic_quest = 'Number of pulses for periodic capture {\bf(may use "e" or "E" for scientific notation)}:';
        stimRate_quest = 'Stimulation rate (pps or Hz) {\bf(min 0.008, max 100,000)}:';
    else
        numOfPulses_quest = 'Number of pulses {\bf(max 32767, 0 or "Inf" for \infty)}:';
        stimRate_quest = 'Stimulation rate (pps or Hz) {\bf(min 0.008, max 100,000; use commas to separate multiple rates)}:';
    end
    if isTriphasic
        promptInputStimParam = {...                   	% input prompts
            'First phase current amplitude (\muA) {\bf(max \pm1,000)}:',...  % first phase amplitude (uA)
            'First phase width (\mus) {\bf(min 5)}:',...              % first phase pulse (us)
            'Second phase current amplitude (\muA) {\bf(max \pm1,000)}:',... % second phase amplitude (uA)
            'Second phase width (\mus) {\bf(min 5)}:',...             % second phase width (us)
            'Third phase current amplitude (\muA) {\bf(max \pm1,000)}:',... % third phase amplitude (uA)
            'Third phase width (\mus) {\bf(min 5)}:',...             % third phase width (us)
            'Interphase delay (\mus):',...                                  % interphase delay (us)
            'Interpulse discharge delay (\mus) {\bf(min 0)}', ...
            stimRate_quest,...   % stimulation rate (Hz)
            numOfPulses_quest, ...       % repetitions
            'Anodic potential bias (V) {\bf(Must have Arduino and Bluetooth set up!): '};
        defaultInputStimParam = { ...
            defaultAmplitude1,defaultPhaseWidth1, ...
            defaultAmplitude2,defaultPhaseWidth2, ...
            defaultAmplitude3,defaultPhaseWidth3, ...
            defaultInterphaseDelay, ...
            defaultDischargeDelay, ...
            defaultStimRate,defaultNumOfPulses, ...
            defaultBias};    % input defaults
    else
        if isLongPulsing
            promptInputStimParam = {...                   	% input prompts
                'First phase current amplitude (\muA) {\bf(max \pm1,000)}:',...  % first phase amplitude (uA)
                'First phase width (\mus) {\bf(min 5)}:',...              % first phase pulse (us)
                'Interphase delay (\mus):',...                                  % interphase delay (us)
                'Second phase current amplitude (\muA) {\bf(max \pm1,000)}:',... % second phase amplitude (uA)
                'Second phase width (\mus) {\bf(min 5; use commas to separate multiple widths)}:',...             % second phase width (us)
                'Interpulse discharge delay (\mus) {\bf(min 0)}', ...
                stimRate_quest,...   % stimulation rate (Hz)
                numOfPulses_quest, ...       % repetitions
                periodic_quest, ...
                'Anodic potential bias (V) {\bf(Must have Arduino and Bluetooth set up!): '};
            defaultInputStimParam = { ...
                defaultAmplitude1,defaultPhaseWidth1, ...
                defaultInterphaseDelay, ...
                defaultAmplitude2,defaultPhaseWidth2, ...
                defaultDischargeDelay, ...
                defaultStimRate, ...
                defaultNumOfPulses,defaultPeriodic, ...
                defaultBias};    % input defaults
        else
            promptInputStimParam = {...                   	% input prompts
                'First phase current amplitude (\muA) {\bf(max \pm1,000)}:',...  % first phase amplitude (uA)
                'First phase width (\mus) {\bf(min 5)}:',...              % first phase pulse (us)
                'Interphase delay (\mus):',...                                  % interphase delay (us)
                'Second phase current amplitude (\muA) {\bf(max \pm1,000)}:',... % second phase amplitude (uA)
                'Second phase width (\mus) {\bf(min 5; use commas to separate multiple widths)}:',...             % second phase width (us)
                'Interpulse discharge delay (\mus) {\bf(min 0)}', ...
                stimRate_quest,...   % stimulation rate (Hz)
                numOfPulses_quest, ...       % repetitions
                'Anodic potential bias (V) {\bf(Must have Arduino and Bluetooth set up!): '};
            defaultInputStimParam = { ...
                defaultAmplitude1,defaultPhaseWidth1, ...
                defaultInterphaseDelay, ...
                defaultAmplitude2,defaultPhaseWidth2, ...
                defaultDischargeDelay, ...
                defaultStimRate,defaultNumOfPulses, ...
                defaultBias};    % input defaults
        end
    end
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
    if isTriphasic
        defaultAmplitude1 = inputStimParam{1};        % first phase amplitude (cell)
        amplitude1 = str2double(defaultAmplitude1);   % first phase amplitude (double)
        defaultPhaseWidth1 = inputStimParam{2};       % first phase width (cell)
        defaultPhaseWidth1 = erase(defaultPhaseWidth1,' ');
        defaultPhaseWidth1 = strrep(defaultPhaseWidth1,',',', ');
        if contains2(defaultPhaseWidth1,',')
            phaseWidth1_raw = defaultPhaseWidth1;
            space_idx = strfind(phaseWidth1_raw,' ');
            phaseWidth1_raw(space_idx) = [];
            phaseWidth1_cell = strsplit(phaseWidth1_raw,',');
            phaseWidth1 = str2double(phaseWidth1_cell );
        else
            phaseWidth1 = str2double(defaultPhaseWidth1); % first phase width (double)            
        end
        numOfPhaseWidth1 = length(phaseWidth1);
        defaultAmplitude2 = inputStimParam{3};        % second phase amplitude (cell)
        amplitude2 = str2double(defaultAmplitude2);	% second phase amplitude (double)
        defaultPhaseWidth2 = inputStimParam{4};       % second phase width (cell)
        defaultPhaseWidth2 = strrep(defaultPhaseWidth2,',',', ');
        if contains2(defaultPhaseWidth2,',')
            phaseWidth2_raw = defaultPhaseWidth2;
            space_idx = strfind(phaseWidth2_raw,' ');
            phaseWidth2_raw(space_idx) = [];
            phaseWidth2_cell = strsplit(phaseWidth2_raw,',');
            phaseWidth2 = str2double(phaseWidth2_cell);
        else
            phaseWidth2 = str2double(defaultPhaseWidth2); % second phase width (double)
        end
        numOfPhaseWidth2 = length(phaseWidth2);
        defaultAmplitude3 = inputStimParam{5};        % third phase amplitude (cell)
        amplitude3 = str2double(defaultAmplitude3);   % third phase amplitude (double)
        defaultPhaseWidth3 = inputStimParam{6};       % third phase width (cell)
        phaseWidth3 = str2double(defaultPhaseWidth3); % third phase width (double)
        defaultInterphaseDelay = inputStimParam{7};          % second phase width (cell)
        defaultInterphaseDelay = erase(defaultInterphaseDelay,' ');
        defaultInterphaseDelay = strrep(defaultInterphaseDelay,',',', ');
        if contains2(defaultInterphaseDelay,',')
            interphaseDelay_raw = defaultInterphaseDelay;
            space_idx = strfind(interphaseDelay_raw,' ');
            interphaseDelay_raw(space_idx) = [];
            interphaseDelay_cell = strsplit(interphaseDelay_raw,',');
            interphaseDelay = str2double(interphaseDelay_cell);
        else
            interphaseDelay = str2double(defaultInterphaseDelay); % interphase delay (double)
        end
        numOfInterphase = length(interphaseDelay);
        defaultDischargeDelay = inputStimParam{8};          % second phase width (cell)
        dischargeDelay = str2double(defaultDischargeDelay); % second phase width (double)
        defaultStimRate = inputStimParam{9};      % stimulation rate (cell)
        stimRate = str2double(defaultStimRate);   % stimulation rate (double)
        numOfStimRate = length(stimRate);
        defaultNumOfPulses = inputStimParam{10};       % number of pulses (cell)
        if isempty(defaultNumOfPulses) || contains2(defaultNumOfPulses,{'0','inf','infty','infinity'})
            numOfPulses = Inf;
        else
            numOfPulses = str2double(defaultNumOfPulses); % number of pulses (double)
        end
        defaultBias = inputStimParam{11};
        bias = str2double(defaultBias);
    else
        defaultAmplitude1 = inputStimParam{1};        % first phase amplitude (cell)
        amplitude1 = str2double(defaultAmplitude1);   % first phase amplitude (double)
        defaultPhaseWidth1 = inputStimParam{2};       % first phase width (cell)
        defaultPhaseWidth1 = erase(defaultPhaseWidth1,' ');
        defaultPhaseWidth1 = strrep(defaultPhaseWidth1,',',', ');
        if contains2(defaultPhaseWidth1,',')
            phaseWidth1_raw = defaultPhaseWidth1;
            space_idx = strfind(phaseWidth1_raw,' ');
            phaseWidth1_raw(space_idx) = [];
            phaseWidth1_cell = strsplit(phaseWidth1_raw,',');
            phaseWidth1 = str2double(phaseWidth1_cell );
        else
            phaseWidth1 = str2double(defaultPhaseWidth1); % first phase width (double)
        end
        numOfPhaseWidth1 = length(phaseWidth1);
        defaultInterphaseDelay = inputStimParam{3};           % interphase delay (cell)
        defaultInterphaseDelay = erase(defaultInterphaseDelay,' ');
        defaultInterphaseDelay = strrep(defaultInterphaseDelay,',',', ');
        if contains2(defaultInterphaseDelay,',')
            interphaseDelay_raw = defaultInterphaseDelay;
            space_idx = strfind(interphaseDelay_raw,' ');
            interphaseDelay_raw(space_idx) = [];
            interphaseDelay_cell = strsplit(interphaseDelay_raw,',');
            interphaseDelay = str2double(interphaseDelay_cell);
        else
            interphaseDelay = str2double(defaultInterphaseDelay); % interphase delay (double)
        end
        numOfInterphase = length(interphaseDelay);
        defaultAmplitude2 = inputStimParam{4};        % second phase amplitude (cell)
        amplitude2 = str2double(defaultAmplitude2);	% second phase amplitude (double)
        defaultPhaseWidth2 = inputStimParam{5};       % second phase width (cell)
        defaultPhaseWidth2 = erase(defaultPhaseWidth2,' ');
        defaultPhaseWidth2 = strrep(defaultPhaseWidth2,',',', ');
        if contains2(defaultPhaseWidth2,',')
            phaseWidth2_raw = defaultPhaseWidth2;
            space_idx = strfind(phaseWidth2_raw,' ');
            phaseWidth2_raw(space_idx) = [];
            phaseWidth2_cell = strsplit(phaseWidth2_raw,',');
            phaseWidth2 = str2double(phaseWidth2_cell);
        else
            phaseWidth2 = str2double(defaultPhaseWidth2); % second phase width (double)
        end
        numOfPhaseWidth2 = length(phaseWidth2);
        defaultDischargeDelay = inputStimParam{6};       % second phase width (cell)
        dischargeDelay = str2double(defaultDischargeDelay); % second phase width (double)
        defaultStimRate = inputStimParam{7};      % stimulation rate (cell)
        defaultStimRate = erase(defaultStimRate,' ');
        defaultStimRate = strrep(defaultStimRate,',',', ');
        if contains2(defaultStimRate,',')
            stimRate_raw = defaultStimRate;
            space_idx = strfind(stimRate_raw,' ');
            stimRate_raw(space_idx) = [];
            stimRate_cell = strsplit(stimRate_raw,',');
            stimRate = str2double(stimRate_cell);
        else
            stimRate = str2double(defaultStimRate);   % stimulation rate (double)
        end
        numOfStimRate = length(stimRate);
        defaultNumOfPulses = inputStimParam{8};       % number of pulses (cell)
        numOfPulses = str2double(defaultNumOfPulses); % number of pulses (double)
        if isempty(numOfPulses) || numOfPulses == 0 || isinf(numOfPulses)
            numOfPulses = Inf;
        else
            
        end
        if isLongPulsing
            defaultPeriodic = inputStimParam{9};       % number of pulses (cell)
            periodic = str2double(defaultPeriodic);
            defaultBias = inputStimParam{10};
            bias = str2double(defaultBias);
        else
            defaultBias = inputStimParam{9};
            bias = str2double(defaultBias);
        end
    end

    % Sign and magnitude
    if isTriphasic
        amplitude_arr = [amplitude1 amplitude2 amplitude3];
        [~,max_idx] = max(abs(amplitude_arr));
        [amplitude_min,min_idx] = min(abs(amplitude_arr));
        amplitude_max = amplitude_arr(max_idx);
        amplitude = amplitude_max;
        
        phaseWidth_arr = [phaseWidth1 phaseWidth2 phaseWidth3];
        phaseWidth_max = phaseWidth_arr(max_idx);
        phaseWidth_min = phaseWidth_arr(min_idx);
        phaseWidth = phaseWidth_max;
    else
        amplitude = amplitude1;
        amplitude_arr = [amplitude1 amplitude2];
        amplitude_min = abs(amplitude);

        phaseWidth = phaseWidth1;
        phaseWidth_arr = [phaseWidth1 phaseWidth2];
        phaseWidth_min = phaseWidth;
    end
    amplitude_sign = sign(amplitude1);	% sign of leading amplitude
    amplitude_ratio = amplitude_arr / amplitude_min;
    phaseWidth_ratio = phaseWidth_arr / phaseWidth_min;

    % Charge
    [chargePhase,chargeInjection] = getCharge(amplitude,phaseWidth,surfaceArea);

    % Check pattern
    if isTriphasic
        [isBalanced,~] = checkPattern( ...
            amplitude1,phaseWidth1, ...
            amplitude2,phaseWidth2, ...
            amplitude3,phaseWidth3);
        isSymmetric = false;
    else
        if numOfPhaseWidth1 ~= numOfPhaseWidth2
            isBalanced = true;
            isSymmetric = false;
        else
            [isBalanced,isSymmetric] = checkPattern( ...
                amplitude1,phaseWidth1, ...
                amplitude2,phaseWidth2);
        end
    end

    % Overlap
    if isTriphasic
        pulseWidth = phaseWidth1 + phaseWidth2 + phaseWidth3 + 2*interphaseDelay;
    else
        pulseWidth = phaseWidth1 + interphaseDelay + phaseWidth2;
    end
    pulseWidth_discharge = transpose(pulseWidth) + dischargeDelay;
    pulseWidth_discharge_s = pulseWidth_discharge * 1e-6;
    stimPeriod = 1 ./ stimRate;
    isOverlap = any(pulseWidth_discharge_s >= stimPeriod);

    % Check waveform
    if ~isBalanced
        warningBalance = '{\bfWAVEFORM IS NOT CHARGE-BALANCED!}';
    else
        warningBalance = [];
    end
    if isOverlap
        warningOverlap = '{\bfWAVEFORM IS OVERLAPPING!}\n';
    else
        warningOverlap = [];
    end
    if numOfPhaseWidth1 > 1 && numOfPhaseWidth2 > 1 ...
            && numOfPhaseWidth1 ~= numOfPhaseWidth2
        warningNotEqual = '{\bfNUMBER OF PHASE WIDTHS MUST BE EQUAL IF BOTH MORE THAN ONE!}';
    else
        warningNotEqual = [];
    end
    if numOfPhaseWidth1 > 1 && numOfPhaseWidth2 > 1 && numOfInterphase > 1 ...
            && (numOfPhaseWidth1 ~= numOfPhaseWidth2) ~= numOfInterphase
        warningInterphase = '{\bfNUMBER OF INTERPHASE DELAYS MUST BE EQUAL TO PHASEWIDTHS IF BOTH MORE THAN ONE!}';
    else
        warningInterphase = [];
    end
    warning = {warningBalance,warningOverlap,warningNotEqual,warningInterphase};
    empty_tf = cellfun(@isempty,warning);
    isBad = ~any(empty_tf);

    % Confirm stimulation parameters
    titleQuestStimParam = 'Confirm';
    % Format questions
    % Charge
    chargeStart_use = sprintf( ...
        '{\\bfStarting {\\itQ}_{ph} = %g nC/ph ({\\itQ}_{inj} = %g mC/cm^{2})}',chargePhase,chargeInjection);
    % Amplitude 1
    amplitude1_use = sprintf( ...
        'First phase amplitude: {\\bf%g \\muA}',amplitude1);% formatted first phase amplitude
    % Phase Width 1
    if numOfPhaseWidth1 > 1
        phaseWidth1_char = strjoin(phaseWidth1_cell,', ');
        phaseWidth1_use = sprintf( ...
            'First phase width: {\\bf%s \\mus}',phaseWidth1_char);  % formatted second phase width
    else
        phaseWidth1_use = sprintf( ...
            'First phase width: {\\bf%g \\mus}',phaseWidth1);% formatted first phase width
    end
    % Amplitude 2
    amplitude2_use = sprintf( ...
        'Second phase amplitude: {\\bf%g \\muA}',amplitude2);      % formatted second phase amplitude
    % Phase Width 2
    if numOfPhaseWidth2 > 1
        phaseWidth2_char = strjoin(phaseWidth2_cell,', ');
        phaseWidth2_use = sprintf( ...
            'First phase width: {\\bf%s \\mus}',phaseWidth2_char);  % formatted second phase width
    else
        phaseWidth2_use = sprintf( ...
            'Second phase width: {\\bf%g \\mus}',phaseWidth2);  % formatted second phase width
    end

    % Triphasic
    if isTriphasic
        amplitude3_use = sprintf( ...
            'Third phase amplitude: {\\bf%g \\muA}',amplitude3);      % formatted third phase amplitude
        phaseWidth3_use = sprintf( ...
            'Third phase width: {\\bf%g \\mus}',phaseWidth3);  % formatted third phase width
    end

    % Interphase Delay
    interphaseDelay_use = sprintf( ...
        'Interphase delay: {\\bf%d \\mus}',interphaseDelay);  % formatted interphase delay

    % Discharge Delay
    dischargeDelay_use = sprintf( ...
        'Time before interpulse discharge: {\\bf%g \\mus}',dischargeDelay);  % formatted before discharge time

    % Stimulation Rate
    if numOfStimRate > 1
        stimRate_char = strjoin(stimRate_cell,', ');
        stimRate_use = sprintf( ...
            'Stimulation rate: {\\bf%s pps}',stimRate_char);  % formatted stimulation rate
    else
        stimRate_use = addCommas(stimRate);
        stimRate_use = sprintf( ...
            'Stimulation rate: {\\bf%s pps}',stimRate_use);         % formatted stimulation rate
    end

    % Number of Pulses
    if isPulsing
        dur_s = numOfPulses / stimRate;
        dur_min = dur_s / 60;
        dur_h = dur_min / 60;
        dur_d = dur_h / 60;
        if dur_d > 1
            dur_use = dur_d;
            dur_unit = 'd';
        elseif dur_h > 1
            dur_use = dur_h;
            dur_unit = 'h';
        elseif dur_min > 1
            dur_use = dur_min;
            dur_unit = 'min';
        else
            dur_use = dur_s;
            dur_unit = 's';
        end
        numOfPulses_use = sprintf( ...
            'Number of pulses for pulsing: {\\bf%d pulses (%.2f %s)}', ...
            numOfPulses,dur_use,dur_unit); % formatted finite repetitions

        if isLongPulsing
        period_s = periodic / stimRate;
        period_min = period_s / 60;
        period_h = period_min / 60;
        period_d = period_h / 60;
        if period_d > 1
            period_use = period_d;
            period_unit = 'd';
        elseif period_h > 1
            period_use = period_h;
            period_unit = 'h';
        elseif period_min > 1
            period_use = period_min;
            period_unit = 'min';
        else
            period_use = period_s;
            period_unit = 's';
        end
        periodic_use = sprintf( ...
            'Number of pulses for periodic capture: {\\bf%d pulses (%.2f %s)}', ...
            periodic,period_use,period_unit); % formatted finite repetitions
        else
            numOfPulses_use = sprintf( ...
                'Number of pulses: {\\bf%d pulses (%.2f %s)}', ...
                numOfPulses,dur_use,dur_unit); % formatted finite repetitions
        end
    else
        if isinf(numOfPulses) || numOfPulses == 0                                   % formatted repetitions
            numOfPulses_use = sprintf( ...
                'Number of pulses: {\\bf\\infty pulses}');   % formatted infinite repetitions
        end
    end
    bias_use = sprintf( ...
        'Anodic potential bias: {\\bf%g V}',bias);  % formatted anodic potential bias

    if isTriphasic
        promptQuestStimParam = {...	% dialog questions
            chargeStart_use,...
            '', ...
            amplitude1_use,...      % inputted first phase amplitude
            phaseWidth1_use,...     % inputted first phase width
            amplitude2_use,...      % inputted second phase amplitude
            phaseWidth2_use,...     % inputted second phase width
            amplitude3_use,...      % inputted third phase amplitude
            phaseWidth3_use,...     % inputted third phase width
            interphaseDelay_use,... % inputted interphase delay
            dischargeDelay_use, ...
            stimRate_use,...        % inputted stimulation rate
            numOfPulses_use, ...    % inputted repetitions
            bias_use};
    else
        if isLongPulsing
            promptQuestStimParam = {...	% dialog questions
                chargeStart_use,...
                '', ...
                amplitude1_use,...      % inputted first phase amplitude
                phaseWidth1_use,...     % inputted first phase width
                interphaseDelay_use,... % inputted interphase delay
                amplitude2_use,...      % inputted second phase amplitude
                phaseWidth2_use,...     % inputted second phase width
                dischargeDelay_use, ...
                stimRate_use,...        % inputted stimulation rate
                numOfPulses_use, ...    % inputted repetitions
                periodic_use, ...
                bias_use};
        else
            promptQuestStimParam = {...	% dialog questions
                chargeStart_use,...
                '', ...
                amplitude1_use,...      % inputted first phase amplitude
                phaseWidth1_use,...     % inputted first phase width
                interphaseDelay_use,... % inputted interphase delay
                amplitude2_use,...      % inputted second phase amplitude
                phaseWidth2_use,...     % inputted second phase width
                dischargeDelay_use, ...
                stimRate_use,...        % inputted stimulation rate
                numOfPulses_use, ...    % inputted repetitions
                bias_use};
        end
    end
    if isBad
        promptQuestStimParam = [
            warning, ...
            '', ...
            promptQuestStimParam]; %#ok<AGROW>
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
            fprintf('Quitting...');     % quitting
            isQuit = true;
            break;                         % exit program
        otherwise                           % cancel
            fprintf('Quitting...');     % quitting
            isQuit = true;
            break;                         % exit program
    end
end

if isQuit
    return;
else
    %% Electrode Polarization
    if interphaseDelay == 0
        polMethod = 'difference';
    else
        confirmMethod = false;
        while ~confirmMethod
            fprintf('Enter method to capture electrode polarization...');
            % Format questions
            promptQuestInput = {'Select method to capture electrode polarization.'};
            % Question box
            questMethod = questdlg(...                      % question dialog
                promptQuestInput,...                       % question prompts
                'Electrode Polarization Method',...                        % question title
                BUTTON_TIME,BUTTON_DIFF,BUTTON_TIME); % buttons

            % Format questions
            promptQuestEnvironment = sprintf( ...
                'Select electrode polarization method: \\bf{%s}', ...
                questMethod);
            % Question box
            questConfirmMethod = questdlg(...                      % question dialog
                promptQuestEnvironment,...                       % question prompts
                'Polarization Electrode Method',...                        % question title
                BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
                opts);                                      % dialog options
            % Confirmation
            switch questConfirmMethod                              % apply choice
                case BUTTON_CONFIRM                         % check confirmation
                    confirmMethod = true;               % confirm info
                    polMethod = questMethod;
                    switch questMethod
                        case BUTTON_TIME
                            tag = 'time';
                        case BUTTON_DIFF
                            tag = 'difference';
                    end
                    fprintf('%s\n',tag);  % info confirmed
                case BUTTON_TRY                             % try again
                    confirmMethod = false;               % trying again
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
    end

    %% Pause
    if isLongPulsing
        isPause = false;
        confirmPause = false;
        while ~confirmPause
            fprintf('Allow pausing...');
            questPause = questdlg('Do you want to pause after periodic capture?', ...
                'Pausing', ...
                BUTTON_NO,BUTTON_YES,BUTTON_NO);

            promptQuestExpType = sprintf('Pause after periodic capture: {\\bf%s}',questPause);
            opts.Default = BUTTON_CONFIRM;
            % Question box
            questConfirmPause = questdlg(...                   % question dialog
                promptQuestExpType,...                    % question prompts
                'Confirm Pausing',...                     % question title
                BUTTON_CONFIRM,BUTTON_TRY,BUTTON_CANCEL,... % buttons
                opts);                                      % dialog options
            % Confirmation
            switch questConfirmPause                       % apply choice
                case BUTTON_CONFIRM                     % check confirmation
                    switch questPause
                        case BUTTON_NO
                            isPause = false;
                        case BUTTON_YES
                            isPause = true;
                    end
                    confirmPause = true;               % confirm parameters
                    fprintf('%s\n',questPause);% parameters confirmed
                case BUTTON_TRY                         % try again
                    confirmPause = false;               % trying again
                    fprintf('Trying again...\n');     % starting over
                case BUTTON_CANCEL                      % quit
                    fprintf('Quitting...\n\n');         % quitting
                    isQuit = true;
                    break;                             % exit program
                otherwise                               % cancel
                    fprintf('Quitting...\n\n');         % quitting
                    isQuit = true;
                    break;                             % exit program
            end
        end
        if isQuit
            return;
        end
    end
end

%% Store
ones_arr = ones(numOfGroups,1);
File.Parameters.Amplitude1 = amplitude1 * ones_arr;
File.Parameters.PhaseWidth1 = phaseWidth1;
File.Parameters.Amplitude2 = amplitude2 * ones_arr;
File.Parameters.PhaseWidth2 = phaseWidth2;
if isTriphasic
    File.Parameters.Amplitude3 = amplitude3 * ones_arr;
    File.Parameters.PhaseWidth3 = phaseWidth3;
end
File.Parameters.InterphaseDelay = interphaseDelay;
File.Parameters.DischargeDelay = dischargeDelay;
File.Parameters.StimulationRate = stimRate;
if isinf(numOfPulses)
    numOfPulses = 0;
end
File.Test.Amplitude = amplitude;
File.Test.ChargePhase = chargePhase;
File.Test.ChargeInjection = chargeInjection;
if isPulsing
    dur_s = numOfPulses / stimRate;
    File.Test.ID = 'pulsing';
    File.Test.Duration = dur_s;
    File.Test.NumberOfPulses = numOfPulses;
    File.Parameters.NumberOfPulses = 0;
    if isLongPulsing
        File.Test.Periodic = periodic;
        File.Parameters.Periodic = periodic;
        File.Test.Pause = isPause;
    end
else
    File.Parameters.NumberOfPulses = numOfPulses;
end
File.Parameters.Bias = bias;
File.Parameters.Polarity = amplitude_sign;
File.Parameters.Symmetry = isSymmetric;
File.Parameters.PulseWidth = pulseWidth;
if interphaseDelay >= 20
    depolTime = 12;
elseif interphaseDelay > 7
    depolTime = 6.5;
else
    depolTime = 0;
end
File.Parameters.Depolarization = depolTime;
File.Parameters.PolarizationMethod = polMethod;
File.Parameters.AmplitudeRatio = amplitude_ratio;
File.Parameters.PhaseWidthRatio = phaseWidth_ratio;

end