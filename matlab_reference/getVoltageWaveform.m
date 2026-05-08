function [...
    voltage,...          	% voltage (V)
    time,...                    % time (s)
    voltageDrive,...            % driving voltage
    maxPotential,...        % max cathodal potential
    isAtVoltageCompliance,...                % is channel broken?
    isPotentialLimitReached,... % is potential limit reached?
    vtPlot,...
    buttonHandle]...            % button handle
    = getVoltageWaveform(...
    scope,...                           % oscilloscope
    channelNum,...                      % channel
    limitPotential,...                  % potential limits
    chargePhase,...                  % charge-per-phase
    chargePhaseLimit,...
    voltageMonScale,... % monitor scaling
    maxPotential_time,...           % time at max cathodal potential
    interphaseDelay)                       % number of repeats
%% Constants
% Conversion
% N_TO_MICRO = 1e6;	% s to us
% N_TO_MILLI = 1e3;	% s to ms
% Rounding
SIG_FIG = 3;
% Waveform check
BROKEN_THRESH = 10;     	% threshold for broken
MAX_REATTEMPTS = 3;
% TIME_NEG_100 = -100e-6;     % prepulse time to check
% TIME_POS_700 = 700e-6;      % postpulse time to check
VOLTAGE_THRESH_CHECK = 0.150;   % prepulse voltage (V) threshold
ACCEPTABLE_EMC_RANGE = 0.010; 	% water window

%% Variables
phaseWidth_s = maxPotential_time - 12e-6;
interphaseDelay_s = interphaseDelay * 1e-6;
% afterInterphaseDelay_s = phaseWidth_s + interphaseDelay_s;
startPhase2_s = 2*phaseWidth_s + interphaseDelay_s;
% numOfSamples = 2;
isWaveformNorm = false;
isVoltageNorm = false;
isAtVoltageCompliance = false;
isPotentialLimitReached = false;
attemptNum = 1;

%% Function
% Acquire time data
time = getTime(scope);
time_us = time / 1e-6;
%     time = getTime(scope) * N_TO_MICRO;
%     time_round = round(time,SIG_FIG,'significant');         % round time
%     time_trunc = floor(time * 1e6);
time_char = cellstr(string(time_us));
time_len = length(time_char);
for idx = 1:time_len
    time_char_val = time_char{idx};
    decimal_idx = strfind(time_char_val,'.');
    time_char_val_decimal = time_char_val(decimal_idx:end);
    time_char{idx} = strrep(time_char_val,time_char_val_decimal,'');
end
time_trunc_s = str2double(time_char) * 1e-6;
time_round_s = round(time_trunc_s,SIG_FIG,'significant');
aftertPhase1_idx = find(time_round_s == phaseWidth_s,1);
maxPotential_idx = find(time_round_s == maxPotential_time,1);        % index of max potential
beforePulse_idx = time_round_s < 0;
% beforePulse_idx_fix = beforePulse_idx(1:end);
afterPulse_idx = time_round_s >= startPhase2_s;
% afterPulse_idx_fix = afterPulse_idx(1:end);
%     phaseWidth_trunc = phaseWidth_s * 1e6;
%     phaseWidth_char = num2str(phaseWidth_trunc);
%     decimal_idx = strfind(phaseWidth_char,'.');
%     phaseWidth_char_val_decimal = time_char_val(decimal_idx:end);
%     phaseWidth_char_new = strrep(phaseWidth_char,phaseWidth_char_val_decimal,'');
%     phaseWidth_round = str2double(phaseWidth_char_new) * 1e-6;

while isWaveformNorm == false
%     if isVoltageNorm == false
%         voltageSamples = [];
%     end
    if isVoltageNorm == false
        voltage = getWaveform(scope,'CH1',voltageMonScale,attemptNum);
    end
    %     fprintf('Acquiring waveform...\n');
%     for sampleNum = 1:numOfSamples
%         if isVoltageNorm == false
%             voltage = getWaveform(scope,'CH1',voltageMonScale,sampleNum,attemptNum);
%             if numOfRepeats > 0
%                 voltageSamples_alloc = [voltageSamples,voltage];
%                 voltageSamples = voltageSamples_alloc;
%                 voltageAvg = mean(voltageSamples,2);
%             else
%                 voltageAvg = voltage;
%             end
% %             fprintf('OK.\n');
%         end
%     end
%     voltage = voltageAvg;
    %     fprintf('Waveform acquired.\n');

    % Voltage Values
    voltageAnte = voltage(beforePulse_idx);
    voltagePost = voltage(afterPulse_idx);
    voltageAnte_mean = mean(voltageAnte,'all');
    voltagePost_mean = mean(voltagePost,'all');
    voltageAnte_sd = std(voltageAnte);
    voltagePost_sd = std(voltagePost);
    if voltageAnte_sd < voltagePost_sd
        openCircuit = voltageAnte_mean;
    else
        openCircuit = voltagePost_mean;
    end
    voltageMin = min(voltage);
    voltageMax = max(voltage);
    %     openCircuit = voltage(1);
    voltageDiff = openCircuit - voltageMin;
    voltageDrive = abs(voltageDiff);% driving voltage
    maxPotential = voltage(maxPotential_idx);  	% max cathodal potential
   
    % Phase 1
    phase1_idx = time_round_s >= 0 & time_round_s < phaseWidth_s;
%     phase1_time = time(phase1_idx);
    phase1_voltage = voltage(phase1_idx);
%     [~,~,r2_1] = getLinReg (phase1_time,phase1_voltage);
    accessVoltage = abs(phase1_voltage(3));

    % Phase 2
%     phase2_idx = time_round_s >= afterInterphaseDelay_s & time_round_s < startPhase2_s;
%     phase2_time = time(phase2_idx);
%     phase2_voltage = voltage(phase2_idx);
%     [~,~,r2_2] = getLinReg (phase2_time,phase2_voltage);
    
     % Check if broken
%     checkDrop = abs(accessVoltage) / openCircuit > 100;
%     checkR2 = r2_1 >= 0.90 && r2_2 >= 0.90;
% %     checkSlope = slope1 == -slope2;
%     if ~checkDrop && checkR2
%         isAtVoltageCompliance = true;
%     end
    isLowerLimitBroken = voltageMin < -BROKEN_THRESH; % lower limit
    isUpperLimitBroken = voltageMax > BROKEN_THRESH;  % upper limit
    if isUpperLimitBroken || isLowerLimitBroken
        isAtVoltageCompliance = true;
    end

    % Values
    fprintf('\tEoc = %.3f V\n',openCircuit);
    fprintf('\tEoc = %.3f V\n',openCircuit);
    fprintf('\tVacc = %.3f V\n',accessVoltage);
    fprintf('\tVdrive = %.3f V\n',voltageDrive);
    voltageDiff = voltageDrive - accessVoltage;
    maxPotential_zero = voltage(aftertPhase1_idx+1);
    if limitPotential < 0
        maxPotential_diff = -voltageDiff;
        fprintf('\tEmc(Vdiff) = %.3f V\n',maxPotential_diff);
        fprintf('\tEmc(t_pw) = %.3f V\n',maxPotential_zero);
        fprintf('\tEmc(t_depol) = %.3f V\n',maxPotential);
    else
        maxPotential_diff = voltageDiff;
        fprintf('\tEma(Vdiff) = %.3f V\n',maxPotential_diff);
        fprintf('\tEma(t_pw) = %.3f V\n',maxPotential_zero);
        fprintf('\tEma(t_depol) = %.3f V\n',maxPotential);
    end

    % Plot
    maxPotential_array = [maxPotential_diff,maxPotential_zero,maxPotential];
    [vtPlot,buttonHandle] = getAcutePlot(...
        channelNum,...              % channel
        chargePhase,...          % charge-per-phase
        time,...                    % time (s)
        voltage,...           	% voltage (V)
        [],...              % current (uA)
        openCircuit,...   % 
        limitPotential,...
        maxPotential_array,...        % max potential
        accessVoltage,...
        voltageDrive,...
        isAtVoltageCompliance);

    if ~ishandle(buttonHandle)   	% stop by button handle
        fprintf('Experiment canceled by user...');
        break;
    end

    % Check waveform
    % prepulse
%     isVoltageAnteStable = checkStability(voltageAnte);
    prepulseVoltage = voltageAnte(1:end-10);               % prepulse voltage
    isPreVoltageTooHigh = any(prepulseVoltage-openCircuit > VOLTAGE_THRESH_CHECK); % check prepulse voltage
    isPreVoltageTooLow = any(prepulseVoltage-openCircuit < -VOLTAGE_THRESH_CHECK); % check prepulse voltage
    isPreVoltageTooOff = isPreVoltageTooHigh || isPreVoltageTooLow;
    % postpulse
%     isVoltagePostStable = checkStability(voltagePost);
%     postpulse_idx = find(time_round == TIME_POS_700,1);         % arbitrary postpulse index
%     postpulseVoltage = abs(voltage(postpulse_idx));              % postpulse voltage
%     isPostVoltageTooOff = postpulseVoltage > VOLTAGE_THRESH_CHECK;% check postpulse voltage
    % check
%     isVoltageTooOff = isPreVoltageTooOff || isPostVoltageTooOff;	% check voltage too high
    if ~isPreVoltageTooOff
        isVoltageNorm = true;
    end
    if isPreVoltageTooOff
        if attemptNum > MAX_REATTEMPTS
            fprintf('Too many attempts to normalize voltage...');
            isWaveformNorm = true;
            fprintf('moving on.\n\n');
        else
            attemptNum = attemptNum + 1;  % increment attempt number
            fprintf('Voltage not normalized...');
            fprintf(scope,'ACQuire:STAte RUN');
            fprintf('trying again.\n');
        end
    else
        fprintf('Voltage normalized...');
        isWaveformNorm = true;
        fprintf('moving on.\n');
    end
%     if isVoltageAnteStable && isVoltagePostStable
%         isVoltageNorm = true;
%     end
%     if ~isVoltageNorm
%         if attemptNum > MAX_REATTEMPTS
%             fprintf('Too many attempts to normalize voltage...');
%             isWaveformNorm = true;
%             fprintf('moving on.\n\n');
%         else
%             attemptNum = attemptNum + 1;  % increment attempt number
%             fprintf('Voltage not normalized...');
%             fprintf(scope,'ACQuire:STAte RUN');
%             fprintf('trying again.\n');
%         end
%     else
%         fprintf('Voltage normalized...');
%         isWaveformNorm = true;
%         fprintf('moving on.\n');
%     end

    % Check if broken
    if isAtVoltageCompliance
        fprintf('Voltage compliance reached...');
        break;
    end

    % Water window
    if limitPotential < 0
        acceptLimitMin = limitPotential - 2.5 * ACCEPTABLE_EMC_RANGE;
        acceptLimitMax = limitPotential + 1.5 * ACCEPTABLE_EMC_RANGE;
    else
        acceptLimitMax = limitPotential + 2.5 * ACCEPTABLE_EMC_RANGE;
        acceptLimitMin = limitPotential - 1.5 * ACCEPTABLE_EMC_RANGE;
    end
    maxPotential_round = round(maxPotential,3);
    isMaxPotentialAboveMin = maxPotential_round >= acceptLimitMin;
    isMaxPotentialBelowMax = maxPotential_round <= acceptLimitMax;
    if isMaxPotentialAboveMin && isMaxPotentialBelowMax	% voltage hits water window
        isPotentialLimitReached = true;
    end
    if chargePhase ~= chargePhaseLimit && ~isinf(chargePhaseLimit)
        maxPotential_diff = abs(maxPotential_round - limitPotential);
        if limitPotential < 0
            if maxPotential_round < limitPotential || maxPotential_diff < 0.050
                isPotentialLimitReached = true;
            end
        else
            if maxPotential_round > limitPotential || maxPotential_diff < 0.050
                isPotentialLimitReached = true;
            end
        end
    end
end

% fprintf(scope,'ACQuire:STAte RUN');

end