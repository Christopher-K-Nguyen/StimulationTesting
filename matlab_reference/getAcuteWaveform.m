function getAcuteWaveform(scope,refElectrode)
%% Constants
% Rounding
SIG_FIG = 3;

%% Variables
phaseWidth_s = 200e-6;
interphaseDelay_s = 100e-6;
afterPhase2_s = 2*phaseWidth_s + interphaseDelay_s;

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
beforePulse_idx = time_round_s < 0;
% beforePulse_idx_fix = beforePulse_idx(1:end-10);
afterPulse_idx = time_round_s >= afterPhase2_s;

% Adjust trigger level
adjustTriggerLevel(scope,amplitude1);

% Current
current = getWaveform2(scope,'CH2');
currentAnte = current(beforePulse_idx);
currentPost = current(afterPulse_idx);
currentAnte_mean = mean(currentAnte,'all');
currentPost_mean = mean(currentPost,'all');
currentOffset = mean([currentAnte_mean currentPost_mean]);
current = current - currentOffset;

% Voltage
voltage = getWaveform2(scope,'CH1');
voltageAnte = voltage(beforePulse_idx);
voltagePost = voltage(afterPulse_idx);
voltageAnte_mean = mean(voltageAnte,'all');
voltagePost_mean = mean(voltagePost,'all');
voltageOffset = mean([voltageAnte_mean voltagePost_mean]);
voltage = voltage - voltageOffset;


% Plot
getPlot2(...
    time,...                    % time (s)
    voltage,...           	% voltage (V)
    current,...              % current (uA)
    refElectrode);

fprintf(scope,'ACQuire:STAte RUN');

end