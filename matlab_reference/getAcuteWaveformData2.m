function [...
    voltage,...          	% voltage (V)
    current,...          	% current (uA)
    time,...                    % time (s)
    accessVoltage1,...
    drivingVoltage1,...            % driving voltage
    maxPotential1,...        % max cathodal potential
    isAtVoltageCompliance,...                % is channel broken?
    isPotentialLimitReached,... % is potential limit reached?
    vtPlot,...
    buttonHandle]...            % button handle
    = getAcuteWaveformData2(...
    scope,...                           % oscilloscope
    channelNum,...                      % channel
    amplitude1,...                  % present current stimulation
    refElectrode,...
    limitPotential,...                  % potential limits
    chargePhase)                  % charge-per-phase
%% Constants
% Rounding
SIG_FIG = 3;
% Waveform check
BROKEN_THRESH = 1.5;     	% threshold for broken
MAX_REATTEMPTS = 3;
% TIME_NEG_100 = -100e-6;     % prepulse time to check
% TIME_POS_700 = 700e-6;      % postpulse time to check
VOLTAGE_THRESH_CHECK = 0.150;   % prepulse voltage (V) threshold
CURRENT_THRESH_CHECK = 15; 	% prepulse current (uA) threshold
ACCEPTABLE_EMC_RANGE = 0.010; 	% water window

%% Variables
phaseWidth_s = 200e-6;
maxPotential1_time_s = phaseWidth_s + 12e-6;
interphaseDelay_s = 100e-6;
afterInterphaseDelay_s = phaseWidth_s + interphaseDelay_s;
afterPhase2_s = 2*phaseWidth_s + interphaseDelay_s;
maxPotential2_time_s = afterPhase2_s + 12e-6;
isWaveformNorm = false;
isVoltageNorm = false;
isCurrentNorm = false;
isAtVoltageCompliance = false;
isPotentialLimitReached = false;
attemptNum = 1;
accessVoltage1 = 0;
drivingVoltage1 = 0;% driving voltage
maxPotential1 = 0;% max cathodal potential

%% Function
% Acquire time data
time = getTime(scope);
%     time = getTime(scope) * N_TO_MICRO;
%     time_round = round(time,SIG_FIG,'significant');         % round time
time_trunc = floor(time * 1e6);
time_char = cellstr(num2str(time_trunc));
time_len = length(time_char);
for idx = 1:time_len
    time_char_val = time_char{idx};
    decimal_idx = strfind(time_char_val,'.');
    time_char_val_decimal = time_char_val(decimal_idx:end);
    time_char{idx} = strrep(time_char_val,time_char_val_decimal,'');
end
time_trunc_s = str2double(time_char) * 1e-6;
time_round_s = round(time_trunc_s,SIG_FIG,'significant');
endPhase1_idx = find(time_round_s == phaseWidth_s,1);
endPhase2_idx = find(time_round_s == afterPhase2_s,1);
maxPotential1_idx = find(time_round_s == maxPotential1_time_s,1);        % index of max potential
maxPotential2_idx = find(time_round_s == maxPotential2_time_s,1);        % index of max potential
beforePulse_idx = time_round_s < 0;
% beforePulse_idx_fix = beforePulse_idx(1:end-10);
afterPulse_idx = time_round_s >= afterPhase2_s;
% afterPulse_idx_fix = afterPulse_idx(10:end);
% phaseWidth_trunc = phaseWidth_s * 1e6;
% phaseWidth_char = num2str(phaseWidth_trunc);
% decimal_idx = strfind(phaseWidth_char,'.');
% phaseWidth_char_val_decimal = time_char_val(decimal_idx:end);
% phaseWidth_char_new = strrep(phaseWidth_char,phaseWidth_char_val_decimal,'');
% phaseWidth_round = str2double(phaseWidth_char_new) * 1e-6;

while isWaveformNorm == false
    % Current
    if isCurrentNorm == false
        % Adjust trigger level
        adjustTriggerLevel(scope,amplitude1);
        current = getWaveform2(scope,'CH2');
        currentAnte = current(beforePulse_idx);
        currentPost = current(afterPulse_idx);
        currentAnte_mean = mean(currentAnte,'all');
        currentPost_mean = mean(currentPost,'all');
        currentOffset = mean([currentAnte_mean currentPost_mean]);
        current = current - currentOffset;
    end
    
    % Voltage
    if isVoltageNorm == false
        voltage = getWaveform2(scope,'CH1');
        voltageAnte = voltage(beforePulse_idx);
        voltagePost = voltage(afterPulse_idx);
        voltageAnte_mean = mean(voltageAnte,'all');
        voltagePost_mean = mean(voltagePost,'all');
        voltageOffset = mean([voltageAnte_mean voltagePost_mean]);
        voltage = voltage - voltageOffset;
    end

    % Phase 1
%     phase1_idx = time_round_s >= 0 & time_round_s <= phaseWidth_s;
%     phase1_time = time(phase1_idx);
%     phase1_voltage = voltage(phase1_idx);
%     [slope1,~,r2_1] = getLinReg (phase1_time,phase1_voltage);
    % access voltage
    [~,accessVoltage1_idx] = min(current);
    accessVoltage1 = abs(voltage(accessVoltage1_idx));
    accessVoltage1_time = time(accessVoltage1_idx);
    fprintf('\tVacc = %.3f V\n',accessVoltage1);
    % driving voltage
    [voltageMin,drivingVoltage1_idx] = min(voltage);
    drivingVoltage1 = abs(voltageMin);% driving voltage
    drivingVoltage1_time = time(drivingVoltage1_idx);
    fprintf('\tVdrive = %.3f V\n',drivingVoltage1);
    voltageDiff1 = drivingVoltage1 - accessVoltage1;
    fprintf('\tVdiff = %.3f V\n',voltageDiff1);
    % max potential
    maxPotential1_zero = voltage(endPhase1_idx);
    maxPotential1_zero_time = time(endPhase1_idx);
    fprintf('\tEmc(pw) = %.3f V\n',maxPotential1_zero);
    maxPotential1 = voltage(maxPotential1_idx);  	% max cathodal potential
    fprintf('\tEmc(depol) = %.3f V\n',maxPotential1);
    % check


    % Phase 2
    phase2_idx = time_round_s >= afterInterphaseDelay_s & time_round_s <= afterPhase2_s;
    phase2_time = time(phase2_idx);
    phase2_voltage = voltage(phase2_idx);
%     [slope2,~,r2_2] = getLinReg (phase2_time,phase2_voltage);
%     endInterphase_idx = find(time == accessVoltage2_time,1) - 10;
    % access voltage
%     [~,accessVoltage2_idx] = max(current);
%     accessVoltage2 = abs(voltage(accessVoltage2_idx));
%     accessVoltage2_time = time(accessVoltage2_idx);
%     accessVoltage2_time = phase2_time(1);
    accessVoltage2_time = phase2_time(1);
    endInterphase_idx = find(time == accessVoltage2_time,1) - 10;
    endInterphase_voltage = voltage(endInterphase_idx);
    accessVoltage2_plot = phase2_voltage(1);
    accessVoltage2 = abs(endInterphase_voltage - accessVoltage2_plot);
    fprintf('\tVacc = %.3f V\n',accessVoltage2);
    % driving voltage
    [voltageMax,drivingVoltage2_idx] = max(voltage);
    drivingVoltage2 = abs(voltageMax);% driving voltage
    drivingVoltage2_time = time(drivingVoltage2_idx);
    fprintf('\tVdrive = %.3f V\n',drivingVoltage2);
    % voltage difference
    voltageDiff2 = drivingVoltage2 - accessVoltage2;
    fprintf('\tVdiff = %.3f V\n',voltageDiff2);
    % max potential
    maxPotential2_zero = voltage(endPhase2_idx);
    maxPotential2_zero_time = time(endPhase2_idx);
    fprintf('\tEma(pw) = %.3f V\n',maxPotential2_zero);
    maxPotential2 = voltage(maxPotential2_idx);  	% max anodal potential
    fprintf('\tEma(depol) = %.3f V\n',maxPotential2);
    % check


    % Check if broken
    isLowerLimitBroken = maxPotential1 < -BROKEN_THRESH; % lower limit
    isUpperLimitBroken = maxPotential2 > BROKEN_THRESH;  % upper limit
    if isUpperLimitBroken || isLowerLimitBroken
        isAtVoltageCompliance = true;
    end

    % Plot
    voltage_arr_time = [...
        accessVoltage1_time,drivingVoltage1_time,...
        accessVoltage2_time,0,drivingVoltage2_time];
    voltage_arr_voltage = [...
        accessVoltage1,drivingVoltage1,...
        accessVoltage2,accessVoltage2_plot,drivingVoltage2];
    voltage_arr = [voltage_arr_time;voltage_arr_voltage];
    voltageDiff_arr = [voltageDiff1;voltageDiff2];
    maxPotential_arr_time = [...
        maxPotential1_zero_time,maxPotential1_time_s,...
        maxPotential2_zero_time,maxPotential2_time_s];
    maxPotential_arr_voltage = [...
        maxPotential1_zero,maxPotential1,...
        maxPotential2_zero,maxPotential2];
    maxPotential_arr = [maxPotential_arr_time;maxPotential_arr_voltage];
    [vtPlot,buttonHandle] = getAcutePlot(...
        channelNum,...              % channel
        amplitude1,...
        chargePhase,...          % charge-per-phase
        time,...                    % time (s)
        voltage,...           	% voltage (V)
        current,...              % current (uA)
        voltage_arr,...
        voltageDiff_arr,...
        maxPotential_arr,...        % max potential
        refElectrode,...
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
    prepulseVoltage = voltageAnte(1:end-20);               % prepulse voltage
    isPreVoltageTooHigh = any(prepulseVoltage-voltageOffset > VOLTAGE_THRESH_CHECK); % check prepulse voltage
    isPreVoltageTooLow = any(prepulseVoltage-voltageOffset < -VOLTAGE_THRESH_CHECK); % check prepulse voltage
    isPreVoltageTooOff = isPreVoltageTooHigh || isPreVoltageTooLow;
    prepulseCurrent = currentAnte(1:end-10);               % prepulse current
    isPreCurrentTooHigh = any(prepulseCurrent > CURRENT_THRESH_CHECK); % check prepulse current
    isPreCurrentTooLow = any(prepulseCurrent < -CURRENT_THRESH_CHECK); % check prepulse current
    isPreCurrentTooOff = isPreCurrentTooHigh || isPreCurrentTooLow;
    % postpulse
    %     postpulse_idx = find(time_round == TIME_POS_700,1);         % arbitrary postpulse index
    %     postpulseVoltage = abs(voltage(postpulse_idx));              % postpulse voltage
    %     isPostVoltageTooOff = postpulseVoltage > VOLTAGE_THRESH_CHECK;% check postpulse voltage
    %     postpulseCurrent = abs(current(postpulse_idx));              % postpulse current
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
        acceptLimitMin = limitPotential - 2 * ACCEPTABLE_EMC_RANGE;
        acceptLimitMax = limitPotential + 2 * ACCEPTABLE_EMC_RANGE;
    else
        acceptLimitMax = limitPotential + 2 * ACCEPTABLE_EMC_RANGE;
        acceptLimitMin = limitPotential - 2 * ACCEPTABLE_EMC_RANGE;
    end
    maxPotential_round = round(maxPotential1,3);
    isMaxPotentialAboveMin = maxPotential_round >= acceptLimitMin;
    isMaxPotentialBelowMax = maxPotential_round <= acceptLimitMax;
    if isMaxPotentialAboveMin && isMaxPotentialBelowMax	% voltage hits water window
        isPotentialLimitReached = true;
    end
%     if chargePhase ~= chargePhaseLimit && ~isinf(chargePhaseLimit)
%         potentialDiff = abs(maxPotential_round - limitPotential);
%         if limitPotential < 0
%             if maxPotential_round < limitPotential || potentialDiff < 0.050
%                 isPotentialLimitReached = true;
%             end
%         else
%             if maxPotential_round > limitPotential || potentialDiff < 0.050
%                 isPotentialLimitReached = true;
%             end
%         end
%     end
end
% fprintf(scope,'ACQuire:STAte RUN');

end