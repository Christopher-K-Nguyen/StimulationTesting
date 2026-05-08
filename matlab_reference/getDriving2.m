function varargout = getDriving2(File,time,voltage_arr,varargin)
%% Variables
numOfVar = length(varargin);
if numOfVar > 0
    var1 = varargin{1};
    className = class(var1);
    switch className
        case 'char'
            if contains2(var1,{'both','all'})
                phase_arr = [1 2];
            elseif contains2(var1,{'first','1st'})
                phase_arr = 1;
            elseif contains2(var1,{'sec','2nd'})
                phase_arr = 2;
            else
                phase_arr = 1;
            end
        case 'double'
            if isempty(var1)
                phase_arr = [1 2];
            else
                phase_arr = var1;
            end
    end
else
    phase_arr = 1;
end

%% Channels
channel_arr = [File.Data(:).ActiveChannel];
groupNum = length(channel_arr);
channelNum = channel_arr(groupNum);
testChannel_arr = File.Parameters.Channels.Test;
% channel_idx = find(testChannel_arr == channelNum,1);

%% Pattern
polarity = File.Parameters.Polarity;
phaseWidth1 = File.Parameters.PhaseWidth1;
interphaseDelay = File.Parameters.InterphaseDelay;
phaseWidth2 = File.Parameters.PhaseWidth2;
pulseWidth = phaseWidth1 + interphaseDelay + phaseWidth2;
depolTime = File.Parameters.Depolarization;

% Phase 1
afterPhaseWidth1_idx = find(time >= phaseWidth1,1);
potentialExcursion1_time = phaseWidth1 + depolTime;
potentialExcursion1_idx = find(time >= potentialExcursion1_time,1);

% Interphase
afterInterphaseDelay = phaseWidth1 + interphaseDelay;
afterInterphaseDelay_idx = find(time >= afterInterphaseDelay,1);

% Pulse Width
afterPulseWidth_idx = find(time >= pulseWidth,1);

%% Time
dataLength = File.Oscilloscope(1).Settings.DataLength;
prePulse_tf = time < 0;
prePulseVoltage = mean(voltage_arr(prePulse_tf));
driving_idx = zeros(1,2);
drivingVoltage = zeros(1,2);
checkTime = 0.5;

%% Algorithm
fprintf('Getting driving...');
startTime = tic;
% figure(channelNum);
% yyaxis left;
for phaseNum = 1:2
    fprintf('Phase %d...%d',phaseNum);
    switch phaseNum
        case 1
            drivingVoltage_idx = afterPhaseWidth1_idx;
            voltage_half = voltage_arr(1:potentialExcursion1_idx);
            switch polarity
                case -1
                    [voltageLimit,~] = min(voltage_half);
                case 1
                    [voltageLimit,~] = max(voltage_half);
            end
        case 2
            drivingVoltage_idx = afterPulseWidth_idx;
            voltage_half = voltage_arr(afterInterphaseDelay_idx:dataLength);
            switch polarity
                case -1
                    [voltageLimit,~] = max(voltage_half);
                case 1
                    [voltageLimit,~] = min(voltage_half);
            end
    end
    voltageLimit_idx = find(voltage_arr == voltageLimit,1);
    while voltageLimit_idx < drivingVoltage_idx
        % isDrivingVoltageGood = false;
        drivingVoltage_plot = voltage_arr(drivingVoltage_idx);
        drivingVoltage_threshCheck = abs(0.05 * drivingVoltage_plot);
        drivingVoltage_check = drivingVoltage_plot;
        try
            drivingCheckTime = tic;
            while ~isDrivingVoltageGood
                idx_arr = [voltageLimit_idx drivingVoltage_check];
                drivingVoltage_idx_check = floor(mean(idx_arr));
                drivingVoltage_check = voltage_arr(drivingVoltage_idx_check);
                drivingVoltage1_diff = abs(drivingVoltage_plot - drivingVoltage_check);
                if drivingVoltage1_diff < drivingVoltage_threshCheck
                    drivingVoltage_idx = drivingVoltage_idx_check;
                    isDrivingVoltageGood = true;
                else %drivingVoltage_idx == drivingVoltage_idx_check
                    drivingVoltage_idx = voltageLimit_idx;
                    break;
                end
                if toc(drivingCheckTime) > checkTime
                    drivingVoltage_idx = voltageLimit_idx;
                    break;
                end
            end
        catch
            drivingVoltage_idx = voltageLimit_idx;
        end
    % else
    %     drivingVoltage_idx = voltageLimit_idx;
    end
    driving_idx(phaseNum) = drivingVoltage_idx;
    % drivingTime = time(drivingVoltage_idx);
    % drivingVoltage_plot = voltage(drivingVoltage_idx);
    % scatter(drivingTime,drivingVoltage_plot,'x','LineWidth',phaseNum);
end

%% Calculation
driving_idx = driving_idx(phase_arr);
drivingVoltage(:) = abs(prePulseVoltage - voltage_arr(driving_idx));
varargout{1} = drivingVoltage;
varargout{2} = driving_idx;

[endTime,unit] = getEndTime(startTime);
fprintf('OK \t\t(%.2f %s)\n',endTime,unit);

end