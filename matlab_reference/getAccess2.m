function varargout = getAccess2(File,time,voltage,varargin)
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
                phase_arr = [1 2];
            end
        case 'double'
            if isempty(var1)
                phase_arr = [1 2];
            else
                phase_arr = var1;
            end
    end
else
    phase_arr = [1 2];
end

%% Channels
channel_arr = [File.Data(:).ActiveChannel];
groupNum = length(channel_arr);

%% Captures
capture_arr = [File.Data(groupNum).Capture(:).Index];
numOfCaptures = length(capture_arr);
captureNum = capture_arr(numOfCaptures);

%% Environment
environment = File.Parameters.Environment;
isAnimal = contains2(environment,'Animal');

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
end
specialField = fieldsList(specialChannel_idx);

for deviceNum = 1:numOfDevices
    fields_cell = File.Oscilloscope(deviceNum).Fields;
    if contains(fields_cell,specialField)
        break;
    end
end
diffTime = File.Oscilloscope(deviceNum).Settings.Interval * 1e6;

%% Pattern
amplitude1 = File.Data(groupNum).Capture(captureNum).Amplitude;
amplitude1_mag = abs(amplitude1);
phaseWidth1 = File.Parameters.PhaseWidth1;
interphaseDelay = File.Parameters.InterphaseDelay;
phaseWidth2 = File.Parameters.PhaseWidth2;
amplitude2 = -amplitude1 * phaseWidth1 / phaseWidth2;
amplitude2_mag = abs(amplitude2);
polarity = File.Parameters.Polarity;
pulseWidth = phaseWidth1 + interphaseDelay + phaseWidth2;
depolTime = File.Parameters.Depolarization;
digitalDelay = File.Stimulator.DigitalDelay;
dischargeDelay = File.Parameters.DischargeDelay;
hasInterphaseDelay = interphaseDelay > 0;
hasDischargeDelay = dischargeDelay > 0;

%% Time
% Prepulse
time0_idx = find(time >= 0,1);
prePulse_tf = time < 0;
prePulseVoltage = mean(voltage(prePulse_tf));

% Trigger
% trig_idx_shift = floor(digitalDelay / diffTime);
if phaseWidth1 < 100
    idx_add1 = 5;
    idx_add2 = 5;
    access1_idx_old = 12;
    access3_idx_old = 8;
    adjust_idx = -6;
    prePhaseTime2 = 3.5;
else
    idx_add1 = 4;
    idx_add2 = 4;
    access1_idx_old = 10;
    access3_idx_old = 11;
    adjust_idx = 0;
    prePhaseTime2 = 6.5;
end
access1_idx_old = access1_idx_old - adjust_idx;
access3_idx_old = access3_idx_old - adjust_idx;
endInterphase_shift_idx = ceil(prePhaseTime2 / diffTime);

% Phase 1
phase1_tf = time >= 0 & time <= phaseWidth1;
phase1_idx = find(phase1_tf);
phase1_len = length(phase1_idx);
accessDerivative1Check_idx = phase1_idx(1) + round(0.15 * phase1_len);
potentialExcursion1_time = phaseWidth1 + depolTime;
potentialExcursion1_idx = find(time >= potentialExcursion1_time,1);

% Interphase
afterInterphaseDelay = phaseWidth1 + interphaseDelay;

% Phase 2
phase2_tf = time >= afterInterphaseDelay & time <= pulseWidth;
phase2_idx = find(phase2_tf);
phase2_len = length(phase2_idx);
accessDerivative2Check_idx = phase2_idx(1) + round(0.15 * phase2_len);
access3_idx_old = access3_idx_old + phase2_idx(1) - 1;

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
accessDerivative1_idx = [];
accessDerivative2_idx = [];
accessDerivative3_idx = [];
accessDerivative4_idx = [];
access1_idx = [];
access2_idx = [];
access3_idx = [];
access4_idx = [];
accessVoltage_arr = zeros(1,4);
accessResistance_arr = zeros(1,4);
checkTime = 0.5;

%% Algorithm
fprintf('Getting access...');
startTime = tic;
for phaseNum = phase_arr
    fprintf('Phase %d...',phaseNum);
    peakPos_thresh = peakMax * 0.8;
    peakNeg_thresh = peakMax * 0.8;
    phaseTime = tic;
    switch phaseNum
        case 1
            % Acess Voltage 1
            try
                while isempty(access1_idx)
                    switch polarity
                        case -1
                            [~,peaksNeg_idx] = findpeaks(-derivative,'MinPeakHeight',peakNeg_thresh);
                            accessDerivative1_idx = min(peaksNeg_idx);
                        case 1
                            [~,peaksPos_idx] = findpeaks(derivative,'MinPeakHeight',peakPos_thresh);
                            accessDerivative1_idx = min(peaksPos_idx);
                    end
                    if isempty(accessDerivative1_idx)...
                            || accessDerivative1_idx < time0_idx ...
                            || accessDerivative1_idx > accessDerivative1Check_idx
                        accessDerivative1_idx = [];
                        switch polarity
                            case -1
                                peakNeg_thresh = peakNeg_thresh * 0.8;
                            case 1
                                peakPos_thresh = peakPos_thresh * 0.8;
                        end
                    else
                        access1_idx = accessDerivative1_idx + idx_add1;
                    end
                    if toc(phaseTime) > checkTime
                        access1_idx = access1_idx_old;
                    end
                end
            catch
                access1_idx = access1_idx_old;
            end
            % accessTime = time(access1_idx);
            accessVoltage_plot = voltage(access1_idx);
            accessVoltage = prePulseVoltage - accessVoltage_plot;
        case 2
            % Access Voltage 2
            if hasInterphaseDelay
                try
                    while isempty(access3_idx)
                        switch polarity
                            case -1
                                [~,peaksPos_idx] = findpeaks(derivative,'MinPeakHeight',peakPos_thresh);
                                accessDerivative2_idx = max(peaksPos_idx);
                            case 1
                                [~,peaksNeg_idx] = findpeaks(-derivative,'MinPeakHeight',peakNeg_thresh);
                                accessDerivative2_idx = max(peaksNeg_idx);
                        end
                        if isempty(accessDerivative2_idx) ...
                                || accessDerivative2_idx < potentialExcursion1_idx ...
                                || accessDerivative2_idx > accessDerivative2Check_idx
                            accessDerivative2_idx = [];
                            switch polarity
                                case -1
                                    peakPos_thresh = peakPos_thresh * 0.8;
                                case 1
                                    peakNeg_thresh = peakNeg_thresh * 0.8;
                            end
                        else
                            access3_idx = accessDerivative3_idx + idx_add2;
                        end
                        if toc(phaseTime) > checkTime
                            access3_idx = access3_idx_old;
                        end
                    end
                catch
                    access3_idx = access3_idx_old;
                end
                % accessTime = time(access3_idx);
                accessVoltage_plot = voltage(access3_idx);
                endInterphase_idx = access3_idx - endInterphase_shift_idx;
                endInterphase_voltage = voltage(endInterphase_idx);
                accessVoltage = endInterphase_voltage - accessVoltage_plot;
            end
    end
    % scatter(accessTime,accessVoltage_plot,'o','LineWidth',phaseNum);
    accessVoltage_arr(phaseNum) = accessVoltage;
    % pause(5);
end
%% Calculation-
if hasInterphaseDelay && hasDischargeDelay
    access_idx = [access1_idx access2_idx access3_idx access4_idx];
    amplitude_mag_arr = [amplitude1_mag amplitude1_mag amplitude2_mag amplitude2_mag];
elseif hasInterphaseDelay
    access_idx = [access1_idx access2_idx access3_idx];
    amplitude_mag_arr = [amplitude1_mag amplitude1_mag amplitude2_mag];
elseif hasDischargeDelay
    access_idx = [access1_idx access4_idx];
    amplitude_mag_arr = [amplitude1_mag amplitude2_mag];
end
for phaseNum = phase_arr
    accessVoltage = accessVoltage_arr(phaseNum);
    amplitude_A = amplitude_mag_arr(phaseNum) * 1e-6;
    accessResistance = accessVoltage ./ amplitude_A / 1e3;
    accessResistance_arr(phaseNum) = accessResistance;
end
varargout{1} = accessVoltage_arr;
varargout{2} = accessResistance_arr;
varargout{3} = access_idx;

[endTime,unit] = getEndTime(startTime);
fprintf('OK \t\t(%.2f %s)\n',endTime,unit);

end