function endPhase_idx = getEndPhase(File,time,voltage,varargin)
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

%% Oscilloscope
numOfDevices = length(File.Oscilloscope);
channelNameList = vertcat(File.Oscilloscope(:).ChannelNames);
fieldsList = vertcat(File.Oscilloscope(:).Fields);
activeChannel_tf = containsi(channelNameList,{'act','work'});
diffChannel_tf = containsi(channelNameList,{'diff'});
voltageChannel_tf = containsi(channelNameList,{'volt'});
if any(activeChannel_tf)
    specialChannel_idx = find(activeChannel_tf);
elseif any(diffChannel_tf)
    specialChannel_idx = find(diffChannel_tf);
elseif any(voltageChannel_tf)
    specialChannel_idx = find(voltageChannel_tf);
else
    specialChannel_idx = 1;
end
specialField = fieldsList(specialChannel_idx);
for deviceNum = 1:numOfDevices
    fields_cell = File.Oscilloscope(deviceNum).Fields;
    if contains(fields_cell,specialField)
        break;
    end
end
trigSource = File.Oscilloscope(deviceNum).Trigger;
isExtTrig = contains2(trigSource,'EXT');

%% Pattern
polarity = File.Parameters.Polarity;
phaseWidth1 = File.Parameters.PhaseWidth1;
interphaseDelay = File.Parameters.InterphaseDelay;
phaseWidth2 = File.Parameters.PhaseWidth2;
pulseWidth = phaseWidth1 + interphaseDelay + phaseWidth2;
depolTime = File.Parameters.Depolarization;

%% Time
% Phase 1
afterPhaseWidth1_idx = find(time >= phaseWidth1,1);
potentialExcursion1_time = phaseWidth1 + depolTime;
potentialExcursion1_idx = find(time >= potentialExcursion1_time,1);

% Phase 2
potentialExcursion2_time = pulseWidth + depolTime;
potentialExcursion2_idx = find(time >= potentialExcursion2_time,1);

% End
afterPulseWidth_idx = find(time >= pulseWidth,1);

%% Derivative
try
    voltage_filt = lowpass(voltage,0.0001);
catch
    voltage_filt = voltage;
end
voltage_diff = diff2(voltage_filt);
time_diff = diff2(time);
try
    derivative_raw = voltage_diff ./ time_diff;
catch
    voltage_diff_len = length(voltage_diff);
    time_diff_len = length(time_diff);
    len = min(voltage_diff_len,time_diff_len);
    voltage_diff_fix = voltage_diff(1:len);
    time_diff_fix = time_diff(1:len);
    derivative_raw = voltage_diff_fix ./ time_diff_fix;
end
derivative = smooth(derivative_raw);
switch polarity
    case -1
        derivative_check = -derivative;
    case 1
        derivative_check = derivative;
end
peakMax = max(derivative_check);
endDerivative1_idx = [];
endDerivative2_idx = [];
endPhase1_idx = [];
endPhase2_idx = [];

%% Driving
zeroCurrent_idx = 12;
% if isExtTrig
    if phaseWidth1 < 100
        adjust1_idx = -5;
        adjust2_idx = 6;
    else
        adjust1_idx = 6;
        adjust2_idx = 6;
    end
% else
%     adjust1_idx = 0;
%     adjust2_idx = 0;
% end
zeroCurrent1_idx = zeroCurrent_idx - adjust1_idx;
zeroCurrent2_idx = zeroCurrent_idx - adjust2_idx;
drivingVoltage1_idx = find(time >= phaseWidth1,1);
drivingVoltage2_idx = find(time >= pulseWidth,1);
endPhase1_idx_old = drivingVoltage1_idx + zeroCurrent1_idx;
endPhase2_idx_old = drivingVoltage2_idx + zeroCurrent2_idx;
checkTime = 1;

%% Algorithm
fprintf('Getting end of phases...');
startTime = tic;
% figure(channelNum);
% yyaxis left;
for phaseNum = phase_arr
    fprintf('Phase %d...%d',phaseNum);
    peakPos_thresh = peakMax * 0.8;
    peakNeg_thresh = peakMax * 0.8;
    phaseTime = tic;
    switch phaseNum
        case 1
            % End Phase 1
            while isempty(endPhase1_idx)
                switch polarity
                    case -1
                        [~,peaksPos_idx] = findpeaks(derivative,'MinPeakHeight',peakPos_thresh);
                        endDerivative1_idx = min(peaksPos_idx);
                    case 1
                        [~,peaksNeg_idx] = findpeaks(-derivative,'MinPeakHeight',peakNeg_thresh);
                        endDerivative1_idx = min(peaksNeg_idx);
                end
                if isempty(endDerivative1_idx)...
                        || endDerivative1_idx > potentialExcursion1_idx ...
                        || endDerivative1_idx < afterPhaseWidth1_idx
                    endDerivative1_idx = [];
                    switch polarity
                        case -1
                            peakNeg_thresh = peakNeg_thresh * 0.9;
                        case 1
                            peakPos_thresh = peakPos_thresh * 0.9;
                    end
                else
                    endPhase1_idx = endDerivative1_idx;
                end
                if toc(phaseTime) > checkTime
                    endPhase1_idx = endPhase1_idx_old;
                end
            end
            endPhase1_idx = endPhase1_idx + 6;
            % endPhaseTime = time(endPhase1_idx);
            % endPhaseVoltage = voltage(endPhase1_idx);

        case 2
            % End Phase 2
            while isempty(endPhase2_idx)
                switch polarity
                    case -1
                        [~,peaksNeg_idx] = findpeaks(-derivative,'MinPeakHeight',peakNeg_thresh);
                        endDerivative2_idx = max(peaksNeg_idx);
                    case 1
                        [~,peaksPos_idx] = findpeaks(derivative,'MinPeakHeight',peakPos_thresh);
                        endDerivative2_idx = max(peaksPos_idx);
                end

                if isempty(endDerivative2_idx) ...
                        || endDerivative2_idx > potentialExcursion2_idx ...
                        || endDerivative2_idx < afterPulseWidth_idx
                    endDerivative2_idx = [];
                    switch polarity
                        case -1
                            peakPos_thresh = peakPos_thresh * 0.9;
                        case 1
                            peakNeg_thresh = peakNeg_thresh * 0.9;
                    end
                else
                    endPhase2_idx = endDerivative2_idx;
                end
                if toc(phaseTime) > checkTime
                    endPhase2_idx = endPhase2_idx_old;
                end
            end
            endPhase2_idx = endPhase2_idx + zeroCurrent1_idx;
            % endPhaseTime = time(endPhase2_idx);
            % endPhaseVoltage = voltage(endPhase2_idx);
    end
    % scatter(endPhaseTime,endPhaseVoltage,'+','LineWidth',phaseNum);
end
endPhase_idx = [endPhase1_idx;endPhase2_idx];

[endTime,unit] = getEndTime(startTime);
fprintf('OK (%.2f %s)\n',endTime,unit);

end