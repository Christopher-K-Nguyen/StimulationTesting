function [...
    voltage,...          	% voltage (V)
    current,...          	% current (uA)
    time,...                    % time (s)
    voltageDrive,...            % driving voltage
    maxPotential,...        % max cathodal potential
    isAtVoltageCompliance,...                % is channel broken?
    isPotentialLimitReached,... % is potential limit reached?
    vtPlot,...
    buttonHandle]...            % button handle
    = getAcuteWaveformData(...
    scope,...                           % oscilloscope
    channelNum,...                      % channel
    amplitude1_new,...                  % present current stimulation
    limitPotential,...                  % potential limits
    chargePhase,...                  % charge-per-phase
    chargePhaseLimit,...
    voltageMonScale,currentMonScale,... % monitor scaling
    maxPotential_time,...           % time at max cathodal potential
    interphaseDelay)                       % number of repeats
%% Constants
% Conversion
MILLI_TO_N = 1e-3;	% mV to V
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
CURRENT_THRESH_CHECK = 15; 	% prepulse current (uA) threshold
ACCEPTABLE_EMC_RANGE = 0.010; 	% water window

%% Variables
phaseWidth_s = maxPotential_time - 12e-6;
interphaseDelay_s = interphaseDelay * 1e-6;
% afterInterphaseDelay_s = phaseWidth_s + interphaseDelay_s;
startPhase2_s = 2*phaseWidth_s + interphaseDelay_s;
currentMonScale_V_uA = currentMonScale * MILLI_TO_N;% mV/uA to V/uA
isWaveformNorm = false;
isVoltageNorm = false;
isCurrentNorm = false;
isAtVoltageCompliance = false;
isPotentialLimitReached = false;
attemptNum = 1;

%% Function
% Adjust trigger level
amplitude1_mag = abs(amplitude1_new);
amplitude1_sign = sign(amplitude1_new);
if amplitude1_mag < 10
    triggerLevel = amplitude1_new * currentMonScale_V_uA + 4.5 * currentMonScale_V_uA * amplitude1_sign;
else
    triggerLevel = amplitude1_new * currentMonScale_V_uA * 0.9;
end
fprintf('Setting trigger Level...');
triggerLevel_text = sprintf('TRIGger:MAIn:LEVel %f',triggerLevel);
fprintf(scope,'TRIGger:MAIn:EDGe:SOUrce CH2');          % trigger source on current
fprintf(scope,triggerLevel_text);
fprintf('%.2e A\n',triggerLevel);

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
afterPhase1_idx = find(time_round_s == phaseWidth_s,1);
maxPotential_idx = find(time_round_s == maxPotential_time,1);        % index of max potential
beforePulse_idx = time_round_s < 0;
% beforePulse_idx_fix = beforePulse_idx(1:end);
afterPulse_idx = time_round_s > startPhase2_s;
% afterPulse_idx_fix = afterPulse_idx(1:end);
%     phaseWidth_trunc = phaseWidth_s * 1e6;
%     phaseWidth_char = num2str(phaseWidth_trunc);
%     decimal_idx = strfind(phaseWidth_char,'.');
%     phaseWidth_char_val_decimal = time_char_val(decimal_idx:end);
%     phaseWidth_char_new = strrep(phaseWidth_char,phaseWidth_char_val_decimal,'');
%     phaseWidth_round = str2double(phaseWidth_char_new) * 1e-6;

% sampleNum = 1;
while isWaveformNorm == false
%     if isVoltageNorm == false
%         voltageSamples = [];
%     end
%     if isCurrentNorm == false
%         currentSamples = [];
%     end
    if isCurrentNorm == false
        current = getWaveform(scope,'CH2',currentMonScale_V_uA,attemptNum);
    end
    if isVoltageNorm == false
       voltage = getWaveform(scope,'CH1',voltageMonScale,attemptNum);
    end
    %     fprintf('Acquiring waveform...\n');
%     for sampleNum = 1:numOfSamples
%         if isCurrentNorm == false
%             current = getWaveform(scope,'CH2',currentMonScale_V_uA,sampleNum,attemptNum);
%             %             current = medfilt1(current,3);
%             if numOfRepeats > 0
%                 currentSamples_alloc = [currentSamples,current];
%                 currentSamples = currentSamples_alloc;
%                 currentAvg = mean(currentSamples,2);
%             else
%                 currentAvg = current;
%             end
%         end
%         current = currentAvg;
%         if isVoltageNorm == false
%            voltage = getWaveform(scope,'CH1',voltageMonScale,sampleNum,attemptNum);
%             if numOfRepeats > 0
%                 voltageSamples_alloc = [voltageSamples,voltage];
%                 voltageSamples = voltageSamples_alloc;
%                 voltageAvg = mean(voltageSamples,2);
%             else
%                 voltageAvg = voltage;
%             end
%         end
%         voltage = voltageAvg;
%     end
    %     fprintf('Waveform acquired.\n');
    
    % Values
    currentAnte = current(beforePulse_idx);
    currentPost = current(afterPulse_idx);
    currentAnte_mean = mean(currentAnte,'all');
    currentPost_mean = mean(currentPost,'all');
    currentAnte_sd = std(currentAnte);
    currentPost_sd = std(currentPost);
    if currentAnte_sd < currentPost_sd
        currentOffset = currentAnte_mean;
    else
        currentOffset = currentPost_mean;
    end
    current = current - currentOffset;
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
%     phase2_idx = time_round_s >= afterInterphaseDelay_s & time_round_s <= startPhase2_s;
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
    fprintf('\tVacc = %.3f V\n',accessVoltage);
    fprintf('\tVdrive = %.3f V\n',voltageDrive);
    voltageDiff = voltageDrive - accessVoltage;
    maxPotential_zero = voltage(afterPhase1_idx+1);
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
    maxPotential_arr = [maxPotential_diff,maxPotential_zero,maxPotential];
    [vtPlot,buttonHandle] = getAcutePlot(...
        channelNum,...          % channel
        chargePhase,...         % charge-per-phase
        time,...                % time (s)
        voltage,...          % voltage (V)
        current,...          % current (uA)
        openCircuit,...
        limitPotential,...
        maxPotential_arr,...        % max potential
        accessVoltage,...
        voltageDrive,...
        isAtVoltageCompliance);

    %     figure(vtPlot);
    %     yline(...
    %         [lowerPotential,upperPotential],...
    %         ':',...
    %         {'Cathodic Potential Limit','Anodic Potential Limit'});

    if ~ishandle(buttonHandle)   	% stop by button handle
        fprintf('Experiment canceled by user...');
        break;
    end

    % Check waveform
    % prepulse
%     isCurrentAnteStable = checkStability(currentAnte);
%     isVoltageAnteStable = checkStability(voltageAnte);
    prepulseVoltage = voltageAnte;               % prepulse voltage
    isPreVoltageTooHigh = any(prepulseVoltage > VOLTAGE_THRESH_CHECK); % check prepulse voltage
    isPreVoltageTooLow = any(prepulseVoltage < -VOLTAGE_THRESH_CHECK); % check prepulse voltage
    isPreVoltageTooOff = isPreVoltageTooHigh || isPreVoltageTooLow;
    prepulseCurrent = currentAnte;               % prepulse current
    isPreCurrentTooHigh = any(prepulseCurrent > CURRENT_THRESH_CHECK); % check prepulse current
    isPreCurrentTooLow = any(prepulseCurrent < -CURRENT_THRESH_CHECK); % check prepulse current
    isPreCurrentTooOff = isPreCurrentTooHigh || isPreCurrentTooLow;
    % postpulse
%     isCurrentPostStable = checkStability(currentPost);
%     isVoltagePostStable = checkStability(voltagePost);
%         postpulse_idx = find(time_round == TIME_POS_700,1);         % arbitrary postpulse index
%         postpulseVoltage = abs(voltage(postpulse_idx));              % postpulse voltage
%         isPostVoltageTooOff = postpulseVoltage > VOLTAGE_THRESH_CHECK;% check postpulse voltage
%         postpulseCurrent = abs(currentAvg(postpulse_idx));              % postpulse current
%         isPostCurrentTooOff = postpulseCurrent > CURRENT_THRESH_CHECK;% check postpulse current
%     % check
%         isVoltageTooOff = isPreVoltageTooOff || isPostVoltageTooOff;	% check voltage too high
%         isCurrentTooOff = isPreCurrentTooOff || isPostCurrentTooOff;  % check current too high
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
%     if isCurrentAnteStable && isCurrentPostStable
%         isCurrentNorm = true;
%     end
%     if isVoltageAnteStable && isVoltagePostStable
%         isVoltageNorm = true;
%     end
%     if ~isCurrentNorm || ~isVoltageNorm
%         if ~isCurrentNorm && ~isVoltageNorm
%             wave1 = 'Voltage and current';
%             wave2 = 'voltage and current';
%         elseif ~isVoltageNorm
%             wave1 = 'Voltage';
%             wave2 = 'voltage';
%         elseif ~isCurrentNorm
%             wave1 = 'Current';
%             wave2 = 'current';
%         end
%         if attemptNum > MAX_REATTEMPTS
%             fprintf('Too many attempts to normalize %s...',wave2);
%             isWaveformNorm = true;
%             fprintf('moving on.\n\n');
%         else
%             attemptNum = attemptNum + 1;  % increment attempt number
%             fprintf('%s not normalized...',wave1);
%             fprintf(scope,'ACQuire:STAte RUN');
%             fprintf('trying again.\n');
%         end
%     else
%         fprintf('Voltage and current normalized...');
%         isWaveformNorm = true;
%         fprintf('moving on.\n');
%     end

    % Check if broken
    if isAtVoltageCompliance
        fprintf('Voltage compliance reached...');
        break;
    end

    % Water window
    %         % upper limit
    %         upperLimitDiff = maxPotential - upperPotential;
    %         upperLimitDiff_mag = abs(upperLimitDiff);
    %         isUpperLimitReached = upperLimitDiff_mag <= ACCEPTABLE_EMC_RANGE;   % reached anodal limit
    %         % lower limit
    %         lowerLimitDiff = maxPotential - lowerPotential;
    %         lowerLimitDiff_mag = abs(lowerLimitDiff);
    %         isLowerLimitReached = lowerLimitDiff_mag <= ACCEPTABLE_EMC_RANGE;   % reached cathodal limit
    %         % check
    %         if isUpperLimitReached || isLowerLimitReached	% voltage hits water window
    %             fprintf('Potential limit reached...')
    %             isPotentialLimitReached = true;
    %             break;
    %         end
    % upper limit

    %         limitDiff = maxPotential - limitPotential;
    %         limitDiff_mag = abs(limitDiff);
    %         isLimitReached = limitDiff_mag <= ACCEPTABLE_EMC_RANGE;   % reached limit
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
    if chargePhase ~= chargePhaseLimit && chargePhaseLimit
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