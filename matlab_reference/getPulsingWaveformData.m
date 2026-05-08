function [...
    voltage,...          	% voltage (V)
    current,...          	% current (uA)
    time,...                    % time (s)
    vtPlot,...
    buttonHandle]...            % button handle
    = getPulsingWaveformData(...
    scope,...                           % oscilloscope
    vtPlot,...
    buttonHandle,...
    chargePhase,...                  % charge-per-phase
    pulseNum,...
    phaseWidth1,...           % time at max cathodal potential
    phaseWidth2,...
    interphaseDelay)                       % number of repeats
%% Constants
% Conversion
% N_TO_MICRO = 1e6;	% s to us
% N_TO_MILLI = 1e3;	% s to ms
% Rounding
SIG_FIG = 3;
% Waveform check
% MAX_REATTEMPTS = 3;
% TIME_NEG_100 = -100e-6;     % prepulse time to check
% TIME_POS_700 = 700e-6;      % postpulse time to check
% VOLTAGE_THRESH_CHECK = 0.150;   % prepulse voltage (V) threshold
% CURRENT_THRESH_CHECK = 15; 	% prepulse current (uA) threshold

%% Variables
phaseWidth1_s = phaseWidth1 * 1e-6;
interphaseDelay_s = interphaseDelay * 1e-6;
phaseWidth2_s = phaseWidth2 * 1e-6;
startPhase2_s = phaseWidth1_s + interphaseDelay_s + phaseWidth2_s;
currentMonScale_V_uA = 1e-3;% mV/uA to V/uA
voltageMonScale = 1;
% isWaveformNorm = false;
% isVoltageNorm = false;
% isCurrentNorm = false;
attemptNum = 1;

%% Function
% Acquire time data
time = getTime(scope);
time_us = time / 1e-6;
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
beforePulse_idx = time_round_s < 0;
% beforePulse_idx_fix = beforePulse_idx(1:end);
afterPulse_idx = time_round_s > startPhase2_s;

% sampleNum = 1;
% while isWaveformNorm == false
%     if isCurrentNorm == false
        current = getWaveform(scope,'CH2',currentMonScale_V_uA,attemptNum);
%     end
%     if isVoltageNorm == false
       voltage = getWaveform(scope,'CH1',voltageMonScale,attemptNum);
%     end
    
    % Values
    currentAnte = current(beforePulse_idx);
    currentPost = current(afterPulse_idx);
    currentAnte_mean = mean(currentAnte,'all');
    currentPost_mean = mean(currentPost,'all');
    currentOffset = mean([currentAnte_mean currentPost_mean]);
    current = current - currentOffset;
    voltageAnte = voltage(beforePulse_idx);
    voltagePost = voltage(afterPulse_idx);
    voltageAnte_mean = mean(voltageAnte,'all');
    voltagePost_mean = mean(voltagePost,'all');
    voltageOffset = mean([voltageAnte_mean voltagePost_mean]);
    voltage = voltage - voltageOffset;

    % Plot
    [vtPlot,buttonHandle] = getPulsingPlot(...
        vtPlot,...
        buttonHandle,...
        chargePhase,...
        pulseNum,...
        time,...
        voltage,...
        current);

    if ~ishandle(buttonHandle)   	% stop by button handle
        fprintf('Experiment canceled by user...');
%         break;
    end

    % Check waveform
    % prepulse
%     prepulseVoltage = voltageAnte;               % prepulse voltage
%     isPreVoltageTooHigh = any(prepulseVoltage > VOLTAGE_THRESH_CHECK); % check prepulse voltage
%     isPreVoltageTooLow = any(prepulseVoltage < -VOLTAGE_THRESH_CHECK); % check prepulse voltage
%     isPreVoltageTooOff = isPreVoltageTooHigh || isPreVoltageTooLow;
%     prepulseCurrent = currentAnte;               % prepulse current
%     isPreCurrentTooHigh = any(prepulseCurrent > CURRENT_THRESH_CHECK); % check prepulse current
%     isPreCurrentTooLow = any(prepulseCurrent < -CURRENT_THRESH_CHECK); % check prepulse current
%     isPreCurrentTooOff = isPreCurrentTooHigh || isPreCurrentTooLow;
%     % postpulse
%     if ~isPreVoltageTooOff
%         isVoltageNorm = true;
%     end
%     if ~isPreCurrentTooOff
%         isCurrentNorm = true;
%     end
%     if isPreVoltageTooOff || isPreCurrentTooOff
%         if isPreVoltageTooOff && isPreCurrentTooOff
%             wave1 = 'Voltage and current';
%             wave2 = 'voltage and current';
%         elseif isPreVoltageTooOff
%             wave1 = 'Voltage';
%             wave2 = 'voltage';
%         elseif isPreCurrentTooOff
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
% end
% fprintf(scope,'ACQuire:STAte RUN');

end