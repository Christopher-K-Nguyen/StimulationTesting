function [...
            voltageAvg,...          	% voltage (V)
            currentAvg,...              % current (uA)
            time,...                    % time (s)
            voltageDrive,...            % driving voltage
            maxCathPotential,...        % max cathodal potential
            buttonHandle,...            % button handle
            vtplot]...            
            = getLongTermWaveformData(...
            scope,...                           % oscilloscope
            channelNum,...                      % channel
            chargePerPhase,...                  % charge-per-phase
            cycleNum,...                        % cycle number
            voltageMonScale,currentMonScale,... % monitor scaling
            maxCathPotential_time,...           % time at max cathodal potential
            numOfRepeats)                       % number of repeats
%% Constants
% Values
FIRST = 1;
NO = 0;
ZERO = 0;
VOLTAGE_CHANNEL = 1;
CURRENT_CHANNEL = 2;
% Conversion
MILLI_TO_N = 1e-3;	% mV to V
% Rounding
SIG_FIG = 3;
% Waveform check
BROKEN_THRESH = 10;	% threshold for broken
ROWS = 2;
MAX_REATTEMPTS = 4;
TIME_NEG_100 = -100e-6;     % prepulse time to check
TIME_POS_700 = 700e-6;      % postpulse time to check
VOLTAGE_THRESH_CHECK = 0.005;   % prepulse voltage threshold
CURRENT_THRESH_CHECK = 0.5;     % prepulse current threshold

%% Variables
numOfSamples = numOfRepeats + 1;
currentMonScale_V_uA = currentMonScale * MILLI_TO_N;% mV/uA to V/uA
isWaveformNorm = 0;
isVoltageNorm = 0;
isCurrentNorm = 0;
attemptNum = 0;

%% Function
while isWaveformNorm == NO
    if isVoltageNorm == NO
        voltageSamples = [];
    end
    if isCurrentNorm == NO
        currentSamples = [];
    end
    for sampleNum = FIRST:numOfSamples
        if sampleNum > ONE || attemptNum > ZERO
            numOfAdjust = 1;
        else
            numOfAdjust = 2;
        end
        % Acquire waveform
        for capture = FIRST:numOfAdjust
            isVISAWorking = 0;
            while isVISAWorking == NO
                try
                    if isVoltageNorm == NO
                        [voltage] = getWaveform(scope,VOLTAGE_CHANNEL,voltageMonScale);
                        if numOfRepeats > ZERO
                            voltageSamples = [voltageSamples voltage];
                            voltageAvg = mean(voltageSamples,ROWS);
                        else
                            voltageAvg = voltage;
                        end
                    end
                    if isCurrentNorm == NO
                        [current] = getWaveform(scope,CURRENT_CHANNEL,currentMonScale_V_uA);
                        if numOfRepeats > ZERO
                            currentSamples = [currentSamples current];
                            currentAvg = mean(currentSamples,ROWS);
                        else
                            currentAvg = current;
                        end
                    end
                    isVISAWorking = 1;
                catch
                    fclose(scope);
                    fopen(scope);
                end
            end
        end
    end

    % Acquire time data
    [time] = getTime(scope);

    % Max voltage values
    voltageDrive = abs(min(voltageAvg));% driving voltage
    voltageMax = abs(max(voltageAvg)); 	% max voltage
    time_round = round(time,SIG_FIG,'significant');         % round time
    findMaxPotential = time_round == maxCathPotential_time; % max cathodal location
    maxCathPotential_idx = find(findMaxPotential,1);        % index of max cathodal potential
    maxCathPotential = voltageAvg(maxCathPotential_idx);  	% max cathodal potential

    % Plot
    [vtplot,buttonHandle] = getLongTermPlot(...
        channelNum,...              % channel
        chargePerPhase,...          % charge-per-phase
        cycleNum,...                % cycle number
        time,...                    % time (s)
        voltage,...                 % voltage (V)
        current,...                 % current (uA)
        maxCathPotential_time,...   % time at max cathodal potential
        maxCathPotential);          % max cathodal potential

    if ~ishandle(buttonHandle)
        fprintf('Canceled by user.\n');
        break;
    end

    % Check if broken
    % upper limit
    isUpperLimitBroken = voltageMax > BROKEN_THRESH;
    % lower limit
    isLowerLimitBroken = voltageDrive < -BROKEN_THRESH;
    % check
    if isUpperLimitBroken || isLowerLimitBroken
        fprintf('Channel is broken--beyond compliance.\n')
        break;                                          % break loop
    end
    
    % Check waveform
    % prepulse
    prepulse_idx = find(time_round == TIME_NEG_100,FIRST);         % arbitrary prepulse index
    prepulseVoltage = abs(voltageAvg(prepulse_idx));               % prepulse voltage
    isPreVoltageTooOff = prepulseVoltage > VOLTAGE_THRESH_CHECK; % check prepulse voltage
    prepulseCurrent = abs(currentAvg(prepulse_idx));               % prepulse current
    isPreCurrentTooOff = prepulseCurrent > CURRENT_THRESH_CHECK; % check prepulse current
    % postpulse
    postpulse_idx = find(time_round == TIME_POS_700,FIRST);         % arbitrary postpulse index
    postpulseVoltage = abs(voltageAvg(postpulse_idx));              % postpulse voltage
    isPostVoltageTooOff = postpulseVoltage > VOLTAGE_THRESH_CHECK;% check postpulse voltage
    postpulseCurrent = abs(currentAvg(postpulse_idx));              % postpulse current
    isPostCurrentTooOff = postpulseCurrent > CURRENT_THRESH_CHECK;% check postpulse current
    % check
    isVoltageTooOff = isPreVoltageTooOff || isPostVoltageTooOff;	% check voltage too high
    isCurrentTooOff = isPreCurrentTooOff || isPostCurrentTooOff;  % check current too high
    if isVoltageTooOff == NO
        isVoltageNorm = 1;
    end
    if isCurrentTooOff == NO
        isCurrentNorm = 1;
    end
    if isVoltageTooOff || isCurrentTooOff
        if attemptNum > MAX_REATTEMPTS
            fprintf('Too many attempts to normalize waveform.\n');
            isWaveformNorm = 1;
        else
            attemptNum = attemptNum + 1;    % increment attempt number
            fprintf('Waveform is not normalized--trying again.\n');
            fprintf(scope,'ACQuire:STAte RUN');
        end
    else
        fprintf('Waveform is normalized--moving on.\n');
        isWaveformNorm = 1;
    end
end

fprintf('Emc = %.3f V\n',maxCathPotential);
fprintf('Vdrive = %.3f V\n',voltageDrive);
disp(' ');
fprintf(scope,'ACQuire:STAte RUN');

end