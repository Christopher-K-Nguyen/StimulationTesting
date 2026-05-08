function varargout = getAccess(File,time,potential_arr)
%% Channels
channel_arr = [File.Data(:).ActiveChannel];
groupNum = length(channel_arr);

%% Captures
capture_arr = [File.Data(groupNum).Capture(:).Index];
numOfCaptures = length(capture_arr);
captureNum = capture_arr(numOfCaptures);

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
polarity = File.Parameters.Polarity;
amplitude1 = File.Parameters.Amplitude1(groupNum);
amplitude1_mag = abs(amplitude1);
amplitude2 = File.Parameters.Amplitude2(groupNum);
amplitude2_mag = abs(amplitude2);
phaseWidth1 = File.Parameters.PhaseWidth1;
interphaseDelay = File.Parameters.InterphaseDelay;
hasInterphaseDelay = interphaseDelay > 0;
phaseWidth2 = File.Parameters.PhaseWidth2;
dischargeDelay = File.Parameters.DischargeDelay;
hasDischargeDelay = dischargeDelay > 0;
pulseWidth = phaseWidth1 + interphaseDelay + phaseWidth2;
depolTime = File.Parameters.Depolarization;

%% Time
% Prepulse
prePulse_tf = time < 0;
prePulsePotenial = mean(potential_arr(prePulse_tf));
prePulseVoltage = mean(voltage_arr(prePulse_tf));
prePulse_len = length(find(prePulse_tf));

% After Parts
afterPhase1_idx = find(time >= phaseWidth1,1);
afterInterphaseDelay = phaseWidth1 + interphaseDelay;
afterInterphaseDelay_idx = find(time >= afterInterphaseDelay,1);
afterPhase2 = afterInterphaseDelay + phaseWidth2;
afterPulseWidth_idx = find(time >= pulseWidth,1);
postPulse_len = length(find(time >= pulseWidth));

% First Phase
time0_idx = find(time <= 0,1,'last');
% phase1_tf = time >= 0 & time < phaseWidth1;
phase1_offset = floor(prePulse_len / 4);

% Interphase
if hasInterphaseDelay
    interphase_tf = time > phaseWidth1 & time < afterInterphaseDelay;
    interphase_len = length(find(interphase_tf));
    interphase_offset = floor(interphase_len / 4);
end

% Second Phase
phase2_tf = time >= afterInterphaseDelay & time <= afterPhase2;
phase2_len = length(find(phase2_tf));
phase2_offset = floor(phase2_len / 2);

% Potential excursion
potentialExcursion1_time = phaseWidth1 + depolTime;
potentialExcursion1_idx = find(time >= potentialExcursion1_time,1);
if hasDischargeDelay
    potentialExcursion2_time = pulseWidth + depolTime;
    potentialExcursion2_idx = find(time >= potentialExcursion1_time,1);
end


%% Derivative
try
    voltage_filt = smooth(lowpass(potential_arr,0.0001));
catch
    voltage_filt = smooth(potential_arr);
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
derivative_check = abs(derivative);
peakMax = max(derivative_check);
if interphaseDelay > 0
    numOfAccess = 4;
else
    numOfAccess = 2;
end
access_idx = NaN(numOfAccess,1);
accessVoltage_arr = access_idx;

%% Access
% Algorithm
fprintf('Getting access...\n');
fprintf('\tDeriving...')
startTime = tic;
if isempty(potential_arr)
    potential_arr = voltage_arr;
end
try
    %         voltage_filt = lowpass(potential_arr,0.0001);
    potential_filt = lowpass(potential_arr,0.0001);
catch
    potential_filt = smooth(potential_arr);
end
potential_diff = diff2(potential_filt);
time_diff = diff2(time);
try
    derivative_raw = potential_diff ./ time_diff;
catch
    voltage_diff_len = length(potential_diff);
    time_diff_len = length(time_diff);
    len = min(voltage_diff_len,time_diff_len);
    potential_diff_fix = potential_diff(1:len);
    time_diff_fix = time_diff(1:len);
    derivative_raw = potential_diff_fix ./ time_diff_fix;
end
derivative = smooth(derivative_raw);

numOfPeaks = 1;
if hasInterphaseDelay
    numOfPeaks = numOfPeaks + 2;
    if hasDischargeDelay
        numOfPeaks = numOfPeaks + 1;
        accessSign_arr = [1 -1 1 -1];
    else
        accessSign_arr = [1 -1 1 NaN];
    end
else
    if hasDischargeDelay
        numOfPeaks = numOfPeaks + 1;
        accessSign_arr = [1 NaN NaN -1];
    else
        accessSign_arr = [1 NaN NaN NaN];
    end
end
if polarity < 0
    accessSign_arr = accessSign_arr * polarity;
end
peak_idx_arr = NaN(1,4);
access_idx_arr = NaN(1,4);
accessVoltage_arr = 4;
[endTime,unit] = getEndTime(startTime);
fprintf('OK (%.2f %s)\n',endTime,unit);

fprintf('\tGetting peaks...');
numOfPeakCheck = 0;
isFound = false;
derivative_check = abs(derivative);
peakMax = max(derivative_check);
peakThresh = peakMax * 0.9;
startTime = tic;
while ~isFound
    [~,peakCheck_idx] = findpeaks(derivative_check,'MinPeakHeight',peakThresh);
    numOfPeakCheck = length(peakCheck_idx);
    if numOfPeakCheck == numOfPeaks
        isFound = true;
    else
        peakThresh = peakThresh * 0.99;
    end
    if toc(startTime) > 60
        isCancel = true;
        break;
    end
    if isFound
    end
end
fprintf('%d...',numOfPeakCheck);
[endTime,unit] = getEndTime(startTime);
fprintf('OK (%.2f %s)\n',endTime,unit);

fprintf('\tGetting access points...');
startTime = tic;
for peakNum = 1:numOfPeakCheck
    peakTime = tic;
    fprintf('%d...',peakNum);
    idx = peakCheck_idx(peakNum);
    idx_fit = idx + lengthTest;
    idx_arr = idx:idx_fit;
    test_arr = derivative_check(idx_arr);
    [slope,intercept] = getLinReg(idx_arr,test_arr);
    isFound = false;
    idx_test = idx + 1;
    thresh = 1;
    dataLength_check = dataLength - 50;
    % figure;
    while ~isFound
        idx_end = idx_test + 30;
        if idx_end > dataLength_check
            idx_end = dataLength_check;
        end
        dataLength_arr = idx_test:idx_end;
        len = length(dataLength_arr);
        fit_arr = slope * dataLength_arr + intercept;
        data_arr = derivative_check(dataLength_arr);
        % clf;
        % yyaxis left;
        % plot(derivative_check);
        % plot(dataLength_arr,fit_arr,'k');
        deviation_arr = zeros(len,1);
        for idx_dev = 1:len
            fit_val = fit_arr(idx_dev);
            data_val = data_arr(idx_dev);
            deviation = abs((fit_val - data_val) ./ fit_val);
            deviation_arr(idx_dev) = deviation;
        end
        % yyaxis right;
        % plot(dataLength_arr,deviation_arr); hold on;
        found_tf = find(deviation_arr > thresh,1);
        if any(found_tf)
            found_idx = find(found_tf,1);
            peakFound_idx = dataLength_arr(found_idx);
            % plot(peakFound_idx,deviation,'LineStyle','none','Marker','+');
            isFound = true;
        else
            % if idx_end == dataLength_check
            %     idx_test = idx + 1;
            %     thresh = thresh + 0.1;
            % else
            idx_test = idx_test + 1;
            % end
        end
        % drawnow;
        % hold off;
        % pause(5);
        if toc(peakTime) > 0.5
            isCancel = true;
            break;
        end
    end
    if isCancel
        isCancel = true;
        break;
    end
    access_idx_arr(peakNum) = peakFound_idx + 1;
end

%         if ~isCancel
% access_idx_arr = peaks_idx;
[endTime,unit] = getEndTime(startTime);
fprintf('OK (%.2f %s)\n',endTime,unit);

% Calculation
fprintf('\tCalculating access...');
updateWaitbar(File,'Calculating access voltage...');
startTime = tic;
drivingPotential_arr = potential_arr(driving_idx_arr);

fprintf('1 (l1)...');
access1_idx = access_idx_arr(1);
accessPotential_1 = potential_arr(access1_idx);
accessVoltage_arr(1) = accessPotential_1 - prePulsePotenial;

if hasInterphaseDelay
    fprintf('2 (t_1)...');
    access2_idx = access_idx_arr(2);
    accessPotential_2 = potential_arr(access2_idx);
    accessVoltage_arr(2) = accessPotential_2 - drivingPotential_arr(1);

    fprintf('3 (l_2)...');
    access3_idx = access_idx_arr(3);
    accessPotential_3 = potential_arr(access3_idx);
    isEndInterphaseFound = false;
    idx = access3_idx - 2;
    while ~isEndInterphaseFound && idx > afterPhase1_idx
        idx_prev = idx - 1;
        value = derivative_check(idx);
        value_prev = derivative_check(idx_prev);

        % Check if the previous value is less than the current value, which would imply an increase
        if value_prev < value
            idx = idx - 1;
        else
            isEndInterphaseFound = true;
            endInterphase_idx = idx;  % Correct placement of endInterphase_idx
        end
    end
    endInterphase = potential_arr(endInterphase_idx);
    accessVoltage_arr(3) = accessPotential_3 - endInterphase;
end

if hasDischargeDelay
    fprintf('4 (t_2)...');
    access4_idx = access_idx_arr(4);
    accessPotential_4 = potential_arr(access4_idx);
    accessVoltage_arr(4) = accessPotential_4 - drivingPotential_arr(2);
end

fprintf('Resistance...');
updateWaitbar(File,'Calculating access resistance...');
accessVoltage_mag_arr = abs(accessVoltage_arr);
amplitude_mag_arr = [ ...
    amplitude1_mag amplitude1_mag ...
    amplitude2_mag amplitude2_mag];
amplitude_A = amplitude_mag_arr * 1e-6;                         % uA to A
accessResistance_Ohm_arr = accessVoltage_mag_arr ./ amplitude_A;% Ohm
accessResistance_arr = accessResistance_Ohm_arr / 1e3;          % Ohm to kOhm

[endTime,unit] = getEndTime(startTime);
fprintf('OK (%.2f %s)\n',endTime,unit);
% else
%     accessVoltage_arr = [];
%     accessResistance_arr = [];
%     access_idx = [];
%     [endTime,unit] = getEndTime(startTime);
%     fprintf('FAILED (%.2f %s)\n',endTime,unit);
% end
%% Output
varargout{1} = accessVoltage_arr;
varargout{2} = accessResistance_arr;
varargout{3} = access_idx;

end