function [...
    voltage,...          	% voltage (V)
    current,...          	% current (uA)
    time,...                    % time (s)
    voltageDrive,...            % driving voltage
    maxPotential,...        % max cathodal potential
    isAtVoltageCompliance,...                % is channel broken?
    isPotentialLimitReached,... % is potential limit reached?
    vtPlot]...
    = getAnimalWaveformData(...
    scope,...                           % oscilloscope
    channelNum,...                      % channel
    amplitude1,...                  % present current stimulation
    chargePhase)                  % charge-per-phase                       
%% Constants
% Conversion
MILLI_TO_N = 1e-3;	% mV to V
% N_TO_MICRO = 1e6;	% s to us
% N_TO_MILLI = 1e3;	% s to ms
% Rounding
SIG_FIG = 3;
% Waveform check
% BROKEN_THRESH = 10;     	% threshold for broken
if amplitude1 == 0
    MAX_REATTEMPTS = 3;
else
    MAX_REATTEMPTS = 5;
end
PHASE_WIDTH = 200e-6;
MAX_POTENTIAL_TIME = 212e-6;
% TIME_NEG_100 = -100e-6;     % prepulse time to check
% TIME_POS_700 = 700e-6;      % postpulse time to check
LOWER_POTENTIAL = -0.8;
VOLTAGE_THRESH_CHECK = 0.100;   % prepulse voltage (V) threshold
CURRENT_THRESH_CHECK = 10; 	% prepulse current (uA) threshold
ACCEPTABLE_EMC_RANGE = 0.010; 	% water window

%% Variables
currentMonScale_V_uA = 1 * MILLI_TO_N;% mV/uA to V/uA
isWaveformNorm = false;
isVoltageNorm = false;
isCurrentNorm = false;
isAtVoltageCompliance = false;
isPotentialLimitReached = false;
attemptNum = 1;

%% Function
% Adjust trigger level
amplitude1_mag = abs(amplitude1);
amplitude1_sign = sign(amplitude1);
if amplitude1_mag < 10
    triggerLevel = amplitude1 * currentMonScale_V_uA + 4.5 * currentMonScale_V_uA * amplitude1_sign;
else
    triggerLevel = amplitude1 * currentMonScale_V_uA * 0.9;
end
fprintf('Setting trigger Level...');
triggerLevel_text = sprintf('TRIGger:MAIn:LEVel %f',triggerLevel);
fprintf(scope,'TRIGger:MAIn:EDGe:SOUrce CH2');          % trigger source on current
fprintf(scope,triggerLevel_text);
fprintf('%.2e A\n',triggerLevel);

while isWaveformNorm == false
    fprintf('Acquiring waveform...\n');
    if isCurrentNorm == false
        current = getWaveform(scope,'CH2',currentMonScale_V_uA,1,attemptNum);
    end
    if isVoltageNorm == false
        voltage = getWaveform(scope,'CH1',1,1,attemptNum);
    end
    fprintf('acquired.\n');
    
    % Acquire time data
    time = getTime(scope);
    %     time = getTime(scope) * N_TO_MICRO;
    time_round = round(time,SIG_FIG,'significant');         % round time
    beforeZero_idx = find(time_round < 0);
    beforeZero_idx_fix = beforeZero_idx(1:end-10);
    % Max voltage values
    voltagePre = voltage(beforeZero_idx_fix);
    openCircuit = mean(voltagePre,'all');
    voltageMin = min(voltage);
    %     voltageMax = max(voltageAvg);
    %     openCircuit = voltageAvg(1);
    voltageDiff = openCircuit - voltageMin;
    voltageDrive = abs(voltageDiff);% driving voltage
    findMaxPotential = time_round == MAX_POTENTIAL_TIME; % max cathodal location
    maxPotential_idx = find(findMaxPotential,1);        % index of max cathodal potential
    maxPotential = voltage(maxPotential_idx);  	% max cathodal potential
    
    % Check if broken
    %     isLowerLimitBroken = voltageMin < -BROKEN_THRESH; % lower limit
    %     isUpperLimitBroken = voltageMax > BROKEN_THRESH;  % upper limit
    %     end
    %     % check
    %     if isUpperLimitBroken || isLowerLimitBroken
    %         isAtVoltageCompliance = true;
    %     end
    % Phase 1
    phase1_idx = find(time_round >= 0 & time_round <= PHASE_WIDTH);
    phase1_time = time(phase1_idx);
    phase1_voltage = voltage(phase1_idx);
    r2_1 = getLinReg (phase1_time,phase1_voltage);
    % Phase 2
    afterInterphaseDelay = PHASE_WIDTH + interphaseDelay;
    afterPhase2 = 2*PHASE_WIDTH + interphaseDelay;
    phase2_idx = find(time_round >= afterInterphaseDelay & time_round <= afterPhase2);
    phase2_time = time(phase2_idx);
    phase2_voltage = voltage(phase2_idx);
    r2_2 = getLinReg (phase2_time,phase2_voltage);
    accessVoltage = phase1_voltage(1) - openCircuit;
    if r2_1 > 0.9 && r2_2 > 0.9
        isAtVoltageCompliance = true;
    end
    fprintf('Eoc = %.3f V\n',openCircuit);
    fprintf('Vacc = %.3f V\n',accessVoltage);
    fprintf('Vdrive = %.3f V\n',voltageDrive);
    fprintf('Emc = %.3f V\n',maxPotential);
    
    % Plot
    [vtPlot] = getAcutePlot(...
        channelNum,...          % channel
        chargePhase,...         % charge-per-phase
        time,...                % time (s)
        voltage,...          % voltage (V)
        current,...          % current (uA)
        openCircuit,...
        maxPotential,...
        accessVoltage,...
        voltageDrive,...
        isAtVoltageCompliance);
    
    % Check waveform
    % prepulse
    prepulseVoltage = voltage(beforeZero_idx_fix);               % prepulse voltage
    isPreVoltageTooHigh = any(prepulseVoltage > VOLTAGE_THRESH_CHECK); % check prepulse voltage
    isPreVoltageTooLow = any(prepulseVoltage < -VOLTAGE_THRESH_CHECK); % check prepulse voltage
    isPreVoltageTooOff = isPreVoltageTooHigh || isPreVoltageTooLow;
    prepulseCurrent = current(beforeZero_idx_fix);               % prepulse current
    isPreCurrentTooHigh = any(prepulseCurrent > CURRENT_THRESH_CHECK); % check prepulse current
    isPreCurrentTooLow = any(prepulseCurrent < -CURRENT_THRESH_CHECK); % check prepulse current
    isPreCurrentTooOff = isPreCurrentTooHigh || isPreCurrentTooLow;
    % postpulse
    %     postpulse_idx = find(time_round == TIME_POS_700,1);         % arbitrary postpulse index
    %     postpulseVoltage = abs(voltageAvg(postpulse_idx));              % postpulse voltage
    %     isPostVoltageTooOff = postpulseVoltage > VOLTAGE_THRESH_CHECK;% check postpulse voltage
    %     postpulseCurrent = abs(currentAvg(postpulse_idx));              % postpulse current
    %     isPostCurrentTooOff = postpulseCurrent > CURRENT_THRESH_CHECK;% check postpulse current
    % check
    %     isVoltageTooOff = isPreVoltageTooOff || isPostVoltageTooOff;	% check voltage too high
    %     isCurrentTooOff = isPreCurrentTooOff || isPostCurrentTooOff;  % check current too high
    if ~isPreVoltageTooOff
        isVoltageNorm = true;
    end
    if ~isPreCurrentTooOff
        isCurrentNorm = true;
    end
    if isPreVoltageTooOff || isPreCurrentTooOff
        if isPreVoltageTooOff && isPreCurrentTooOff
            wave1 = 'Voltage and current';
            wave2 = 'voltage and current';
        elseif isPreVoltageTooOff
            wave1 = 'Voltage';
            wave2 = 'voltage';
        elseif isPreCurrentTooOff
            wave1 = 'Current';
            wave2 = 'current';
        end
        if attemptNum > MAX_REATTEMPTS
            fprintf('Too many attempts to normalize %s...',wave2);
            isWaveformNorm = true;
            fprintf('moving on.\n\n');
        else
            attemptNum = attemptNum + 1;  % increment attempt number
            fprintf('%s not normalized...',wave1);
            fprintf(scope,'ACQuire:STAte RUN');
            fprintf('trying again.\n');
        end
    else
        fprintf('Voltage and current normalized...');
        isWaveformNorm = true;
        fprintf('moving on.\n');
    end
    
    % Check if broken
    if isAtVoltageCompliance
        fprintf('Voltage compliance reached...');
    end
    
    % Water window
    acceptLimitMin = LOWER_POTENTIAL - 2 * ACCEPTABLE_EMC_RANGE;
    acceptLimitMax = LOWER_POTENTIAL + 1 * ACCEPTABLE_EMC_RANGE;
    maxPotential_round = round(maxPotential,3);
    isMaxPotentialAboveMin = maxPotential_round >= acceptLimitMin;
    isMaxPotentialBelowMax = maxPotential_round <= acceptLimitMax;
    if ~isAtVoltageCompliance && amplitude1 ~= 0
        if isMaxPotentialAboveMin && isMaxPotentialBelowMax	% voltage hits water window
            isPotentialLimitReached = true;
        end
        if maxPotential_round < LOWER_POTENTIAL || potentialDiff < 0.050
            isPotentialLimitReached = true;
        end
    end
    % fprintf(scope,'ACQuire:STAte RUN');
    
end

end