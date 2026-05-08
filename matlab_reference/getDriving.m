function [drivingVoltage,driving_idx] = getDriving(File,time,voltage,varargin)
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

%% Pattern
polarity = File.Parameters.Polarity;
phaseWidth1 = File.Parameters.PhaseWidth1;
pulseWidth = File.Parameters.PulseWidth;
depolTime = File.Parameters.Depolarization;

% Phase 1
potentialExcursion1_time = phaseWidth1 + depolTime;
potentialExcursion1_idx = find(time >= potentialExcursion1_time,1);

%% Time
dataLength = length(time);
prePulse_idx = time < 0;
prePulseVoltage = mean(voltage(prePulse_idx));
drivingVoltage1_idx = find(time >= phaseWidth1,1);
drivingVoltage2_idx = find(time >= pulseWidth,1);
driving_idx = [drivingVoltage1_idx drivingVoltage2_idx];

%% Algorithm
fprintf('Getting driving...');

startTime = tic;
% Phase 1
voltage_half1 = voltage(1:potentialExcursion1_idx);
[~,voltageMin_idx]  = min(voltage_half1);
if voltageMin_idx < drivingVoltage1_idx
    isDrivingVoltage1Good = false;
    drivingVoltage1_treshCheck = abs(0.05 * drivingVoltage1_plot);
    drivingVoltage1_check = drivingVoltage1_plot;
    try
        drivingCheckTime = tic;
        while ~isDrivingVoltage1Good
            idx_arr = [voltageMin_idx drivingVoltage1_check];
            drivingVoltage1_idx_check = floor(mean(idx_arr));
            drivingVoltage1_check = voltage(drivingVoltage1_idx_check);
            drivingVoltage1_diff = abs(drivingVoltage1_plot - drivingVoltage1_check);
            if drivingVoltage1_diff < drivingVoltage1_treshCheck
                drivingVoltage1_idx = drivingVoltage1_idx_check;
                isDrivingVoltage1Good = true;
            elseif drivingVoltage1_idx == drivingVoltage1_idx_check
                drivingVoltage1_idx = voltageMin_idx;
                break;
            end
            if toc(drivingCheckTime) > 1
                drivingVoltage1_idx = voltageMin_idx;
                break;
            end
        end
    catch
        drivingVoltage1_idx = voltageMin_idx;
    end
else
    drivingVoltage1_idx = voltageMin_idx;
end

% Phase 2
voltage_half2 = voltage(potentialExcursion1_idx:dataLength);
[voltageMax,~] = max(voltage_half2);
voltageMax_idx = find(voltage == voltageMax,1);
if voltageMax_idx < drivingVoltage2_idx
    drivingVoltage2_treshCheck = abs(0.05 * drivingVoltage2_plot);
    drivingVoltage2_check = drivingVoltage2_plot;
    isDrivingVoltage2Good = false;
    try
        drivingCheckTime = tic;
        while ~isDrivingVoltage2Good
            idx_arr = [voltageMax_idx drivingVoltage2_check];
            drivingVoltage2_idx_check = floor(mean(idx_arr));
            drivingVoltage2_check = voltage(drivingVoltage2_idx_check);
            drivingVoltage2_diff = abs(drivingVoltage2_plot - drivingVoltage2_check);
            if drivingVoltage2_diff < drivingVoltage2_treshCheck
                drivingVoltage2_idx = drivingVoltage2_idx_check;
                isDrivingVoltage2Good = true;
            elseif drivingVoltage2_idx == drivingVoltage2_idx_check
                drivingVoltage2_idx = voltageMax_idx;
                break;
            end
            if toc(drivingCheckTime) > 0.5
                drivingVoltage2_idx = voltageMax_idx;
                break;
            end
        end
    catch
        drivingVoltage2_idx = voltageMax_idx;
    end
else
    drivingVoltage2_idx = voltageMax_idx;
end

%% Calculation
fprintf('calculating...');
driving_idx = driving_idx(phase_arr);
drivingVoltage = abs(prePulseVoltage - voltage(driving_idx));

[endTime,unit] = getEndTime(startTime);
fprintf('OK (%.2f %s)\n',endTime,unit);

end