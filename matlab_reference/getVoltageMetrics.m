function varargout = getVoltageMetrics(File,time,potential_arr,varargin)
%% Constants
FIT_PADDING = 5;

%% Variables
isCancel = false;
drivingVoltage_arr = [];
driving_idx_arr = [];
accessVoltage_arr = [];
accessResistance_arr = [];
access_idx_arr = [];

%% Input
if isempty(varargin)
    voltage_arr = potential_arr;
else
    voltage_arr = varargin{1};
end

%% Experiment
expType = File.Test.Experiment;
isTriphasic = contains2(expType,'TV');
checkTime = 0.5;
if isTriphasic
    numOfPhases = 3;
else
    numOfPhases = 2;
end
% Polarization
polMethod = File.Parameters.PolarizationMethod;
isPolAtTime = contains2(polMethod,'time');

%% Channels
channel_arr = [File.Data(:).ActiveChannel];
groupNum = length(channel_arr);

%% Oscilloscope
numOfDevices = length(File.Oscilloscope);
diffTime = File.Oscilloscope(1).Settings.Interval * 1e6;
dataLength = File.Oscilloscope(1).Settings.DataLength;
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

%% Pattern
polarity = File.Parameters.Polarity;
amplitude1 = File.Parameters.Amplitude1(groupNum);
amplitude1_mag = abs(amplitude1);
amplitude2 = File.Parameters.Amplitude2(groupNum);
amplitude2_mag = abs(amplitude2);
phaseWidth1 = File.Parameters.PhaseWidth1;
phaseWidth2 = File.Parameters.PhaseWidth2;
if isTriphasic
    amplitude3 = File.Parameters.Amplitude3(groupNum);
    amplitude3_mag = abs(amplitude3);
    phaseWidth3 = File.Parameters.PhaseWidth3;
else
    amplitude3_mag = NaN;
    phaseWidth3 = 0;
end
interphaseDelay = File.Parameters.InterphaseDelay;
hasInterphaseDelay = interphaseDelay > 0;
dischargeDelay = File.Parameters.DischargeDelay;
hasDischargeDelay = dischargeDelay > 0;
if isTriphasic
    pulseWidth = phaseWidth1 + phaseWidth2 + phaseWidth3 + 2*interphaseDelay;
else
    pulseWidth = phaseWidth1 + interphaseDelay + phaseWidth2;
end
depolTime = File.Parameters.Depolarization;

% if hasInterphaseDelay
%     amplitude_mag_arr = [ ...
%         amplitude1_mag amplitude1_mag ...
%         amplitude2_mag amplitude2_mag];
%     if isTriphasic
%         amplitude_mag_arr = [ ...
%             amplitude_mag_arr ...
%             amplitude3_mag amplitude3_mag];
%     end
% else
%     if isTriphasic
%         amplitude_mag_arr = [amplitude1_mag amplitude3_mag];
%     else
%         amplitude_mag_arr = [amplitude1_mag amplitude2_mag];
%     end
% end
amplitude_mag_arr = [ ...
        amplitude1_mag amplitude1_mag ...
        amplitude2_mag amplitude2_mag ...
        amplitude3_mag amplitude3_mag];
%% Time
% Differntial
% endInterphaseShift_idx = ceil(200 / phaseWidth1 * 1.5);
endInterphaseShift_idx = ceil(0.5 / diffTime);
endInterphase1_idx = [];
if isTriphasic
    endInterphase2_idx = [];
end

% Prepulse
prePulse_tf = time < 0;
prePulsePotenial = mean(potential_arr(prePulse_tf));
prePulseVoltage = mean(voltage_arr(prePulse_tf));
prePulse_len = length(find(prePulse_tf));

% After Parts
afterPhase1_idx = find(time >= phaseWidth1,1);
afterInterphaseDelay1 = phaseWidth1 + interphaseDelay;
afterInterphaseDelay1_idx = find(time >= afterInterphaseDelay1,1);
afterPhase2 = afterInterphaseDelay1 + phaseWidth2;
afterPhase2_idx = find(time >= afterPhase2,1);
if isTriphasic
    afterInterphaseDelay2 = afterPhase2 + interphaseDelay;
    afterInterphaseDelay2_idx = find(time >= afterInterphaseDelay2,1);
    afterPhase3 = afterInterphaseDelay2 + phaseWidth3;
    afterPhase3_idx = find(time >= afterPhase3,1);
end
afterPulseWidth_idx = find(time >= pulseWidth,1);
postPulse_len = length(find(time >= pulseWidth));

% First Phase
time0_idx = find(time <= 0,1,'last');
% phase1_tf = time >= 0 & time < phaseWidth1;
phase1_offset = floor(prePulse_len / 4);

% Interphase
if hasInterphaseDelay
    interphase1_tf = time > phaseWidth1 & time < afterInterphaseDelay1;
    interphase1_len = length(find(interphase1_tf));
    interphase1_offset = floor(interphase1_len / 4);
    if isTriphasic
        interphase2_tf = time > phaseWidth3 & time < afterInterphaseDelay2;
        interphase2_len = length(find(interphase2_tf));
        interphase2_offset = floor(interphase2_len / 4);
    end
end

% Second Phase
phase2_tf = time >= afterInterphaseDelay1 & time <= afterPhase2;
phase2_len = length(find(phase2_tf));
phase2_offset = floor(phase2_len / 2);

% Potential excursion
potentialExcursion1_time = phaseWidth1 + depolTime;
potentialExcursion1_idx = find(time >= potentialExcursion1_time,1);
if hasDischargeDelay
    if isTriphasic
        potentialExcursion2_time = afterPhase2 + depolTime;
        potentialExcursion2_idx = find(time >= potentialExcursion2_time,1);
        potentialExcursion3_time = afterPhase3 + depolTime;
        potentialExcursion3_idx = find(time >= potentialExcursion3_time,1);
    else
        potentialExcursion2_time = afterPhase2 + depolTime;
        potentialExcursion2_idx = find(time >= potentialExcursion2_time,1);
    end

% End
totalPulse = pulseWidth + dischargeDelay;
totalPulse_idx = find(time >= totalPulse,1,'first');
if isempty(totalPulse_idx)
    totalPulse_idx = find(time <= totalPulse,1,'last');
end

% try
%% Driving
fprintf('Getting driving...');
startTime = tic;
% figure(channelNum);
% yyaxis left;
% Driving
driving_idx_arr = zeros(numOfPhases,1);
drivingVoltage_arr = zeros(numOfPhases,1);
for phaseNum = 1:numOfPhases
    fprintf('Phase %d...',phaseNum);
    switch phaseNum
        case 1
            driving_idx = afterPhase1_idx;
            if isPolAtTime
                ending_idx = potentialExcursion1_idx;
            else
                ending_idx = afterInterphaseDelay1_idx + 1;
            end
            voltage_part = voltage_arr(1:ending_idx);
            switch polarity
                case -1
                    [voltageLimit,~] = min(voltage_part);
                case 1
                    [voltageLimit,~] = max(voltage_part);
            end
        case 2
            driving_idx = afterPhase2_idx;
            if hasInterphaseDelay
                starting_idx = afterInterphaseDelay1_idx + 3;
            else
                starting_idx = afterPhase1_idx + 1;
            end
            if isTriphasic
                if isPolAtTime
                    ending_idx = potentialExcursion2_idx;
                else
                    ending_idx = afterInterphaseDelay2_idx + 1;
                end
            else
                ending_idx = dataLength;
            end
            voltage_part = voltage_arr(starting_idx:ending_idx);
            switch polarity
                case -1
                    [voltageLimit,~] = max(voltage_part);
                case 1
                    [voltageLimit,~] = min(voltage_part);
            end
        case 3
            driving_idx = afterPhase3_idx;
            if hasInterphaseDelay
                starting_idx = afterInterphaseDelay2_idx + 3;
            else
                starting_idx = afterPhase2_idx + 1;
            end
            if isPolAtTime
                ending_idx = potentialExcursion3_idx;
            else
                ending_idx = dataLength;
            end
            voltage_part = voltage_arr(starting_idx:ending_idx);
            switch polarity
                case -1
                    [voltageLimit,~] = min(voltage_part);
                case 1
                    [voltageLimit,~] = max(voltage_part);
            end
    end
    voltageLimit_idx = find(voltage_arr == voltageLimit,1);
    while voltageLimit_idx < driving_idx
        % isDrivingVoltageGood = false;
        drivingVoltage_plot = voltage_arr(driving_idx);
        drivingVoltage_threshCheck = abs(0.05 * drivingVoltage_plot);
        % drivingVoltage_check = drivingVoltage_plot;
        try
            drivingCheckTime = tic;
            while ~isDrivingVoltageGood
                idx_arr = [voltageLimit_idx driving_idx];
                drivingVoltage_idx_check = floor(mean(idx_arr));
                drivingVoltage_check = voltage_arr(drivingVoltage_idx_check);
                drivingVoltage1_diff = abs(drivingVoltage_plot - drivingVoltage_check);
                if drivingVoltage1_diff < drivingVoltage_threshCheck
                    driving_idx = drivingVoltage_idx_check;
                    isDrivingVoltageGood = true;
                else
                    driving_idx = voltageLimit_idx;
                    break;
                end
                if toc(drivingCheckTime) > checkTime
                    driving_idx = voltageLimit_idx;
                    break;
                end
            end
        catch
            driving_idx = voltageLimit_idx;
        end
    end
    driving_idx_arr(phaseNum) = driving_idx;
end
[endTime,unit] = getEndTime(startTime);
fprintf('OK \t\t(%.2f %s)\n',endTime,unit);

%% Access
% Algorithm
fprintf('Getting access...\n');
fprintf('\tDeriving...')
startTime = tic;
% if ~isempty(potential_arr)
%     test_arr = potential_arr;
% else
    test_arr = voltage_arr;
% end

try
    %         voltage_filt = lowpass(potential_arr,0.0001);
    test_filt = lowpass(test_arr,0.0001);
catch
    test_filt = smooth(test_arr,8);
end
test_diff = diff2(test_filt);
time_diff = diff2(time);
% try
    derivative_raw = test_diff ./ time_diff;
% catch
%     test_diff_len = length(test_diff);
%     time_diff_len = length(time_diff);
%     len = min(test_diff_len,time_diff_len);
%     potential_diff_fix = test_diff(1:len);
%     time_diff_fix = time_diff(1:len);
%     derivative_raw = potential_diff_fix ./ time_diff_fix;
% end
derivative_fix = derivative_raw(1:totalPulse_idx);
derivative = smooth(derivative_fix,8);

% Determine number of access voltage peaks
if isTriphasic
    if hasInterphaseDelay
        numOfPeaks = 6;
    else
        numOfPeaks = 2;
    end
else
    if hasInterphaseDelay
        numOfPeaks = 4;
    else
        numOfPeaks = 2;
    end
end
[endTime,unit] = getEndTime(startTime);
fprintf('OK \t\t(%.2f %s)\n',endTime,unit);

fprintf('\tGetting peaks...');
numOfPeakCheck = 0;
access_idx_arr = NaN(1,6);
accessVoltage_arr = NaN(1,6);
accessResistance_arr = NaN(1,6);
isFound = false;
derivative_check = abs(derivative);
check_len = length(derivative_check);
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
    if toc(startTime) > 5
        isCancel = true;
        break;
    end
    %     if isFound
    %     end
end
if isCancel
    [endTime,unit] = getEndTime(startTime);
    fprintf('FAILED (%.2f %s)\n',endTime,unit);
else
    fprintf('%d...',numOfPeakCheck);
    [endTime,unit] = getEndTime(startTime);
    fprintf('OK \t\t(%.2f %s)\n',endTime,unit);

    fprintf('\tGetting access points...');
    startTime = tic;
    for peakNum = 1:numOfPeakCheck
        peakTime = tic;
        fprintf('%d...',peakNum);
        idx = peakCheck_idx(peakNum);
        if peakNum == 1 && idx < time0_idx - 10
            idx = time0_idx;
        end
        idx_fit = idx + FIT_PADDING;
        if idx_fit > check_len
            idx_fit = check_len;
        end
        idx_arr = idx:idx_fit;
            regTest_arr = derivative_check(idx_arr);
            [slope,intercept] = getLinReg(idx_arr,regTest_arr);
            isFound = false;
            idx_test = idx + 1;
            thresh = 1;
            % figure;
            while ~isFound
                idx_end = idx_test + 30;
                if idx_end > totalPulse_idx
                    idx_end = totalPulse_idx;
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
                if toc(peakTime) > 5
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

    if ~isCancel
        % access_idx_arr = peaks_idx;
        [endTime,unit] = getEndTime(startTime);
        fprintf('OK \t\t(%.2f %s)\n',endTime,unit);

        % Calculation
        fprintf('\tCalculating access...');
        if ~hasDischargeDelay
            access_idx_arr(numOfPeaks) = [];
            numOfAccess = numOfPeaks - 1;
        else
            numOfAccess = numOfPeaks;
        end
        updateWaitbar(File,'Calculating access voltage...');
        startTime = tic;
        drivingPotential_arr = potential_arr(driving_idx_arr);

        access1_idx = access_idx_arr(1);
        accessPotential_1 = potential_arr(access1_idx);
        accessVoltage_arr(1) = accessPotential_1 - prePulsePotenial;
        fprintf('1...');

        if hasInterphaseDelay
            access2_idx = access_idx_arr(2);
            accessPotential_2 = potential_arr(access2_idx);
            accessVoltage_arr(2) = accessPotential_2 - drivingPotential_arr(1);
            fprintf('2...');

            access3_idx = access_idx_arr(3);
            accessPotential_3 = potential_arr(access3_idx);
            isEndInterphaseFound = false;
            idx = access3_idx - endInterphaseShift_idx;
            while ~isEndInterphaseFound
                idx_prev = idx - 1;
                value = derivative_check(idx);
                value_prev = derivative_check(idx_prev);

                % Check if the previous value is less than the current value, which would imply an increase
                if value_prev < value
                    idx = idx - 1;
                else
                    isEndInterphaseFound = true;
                    endInterphase1_idx = idx;  % Correct placement of endInterphase_idx
                end
            end
            endInterphase1 = potential_arr(endInterphase1_idx);
            accessVoltage_arr(3) = accessPotential_3 - endInterphase1;
            fprintf('3...');

            if isTriphasic
                access4_idx = access_idx_arr(4);
                accessPotential_4 = potential_arr(access4_idx);
                accessVoltage_arr(4) = accessPotential_4 - drivingPotential_arr(2);
                fprintf('4...');

                access5_idx = access_idx_arr(5);
                accessPotential_5 = potential_arr(access5_idx);
                isEndInterphaseFound = false;
                idx = access5_idx - endInterphaseShift_idx;
                while ~isEndInterphaseFound
                    idx_prev = idx - 1;
                    value = derivative_check(idx);
                    value_prev = derivative_check(idx_prev);

                    % Check if the previous value is less than the current value, which would imply an increase
                    if value_prev < value
                        idx = idx - 1;
                    else
                        isEndInterphaseFound = true;
                        endInterphase2_idx = idx;  % Correct placement of endInterphase_idx
                    end
                end
                endInterphase2 = potential_arr(endInterphase2_idx);
                accessVoltage_arr(5) = accessPotential_5 - endInterphase2;
                fprintf('5...');
            end
        end

        if hasDischargeDelay
            if isTriphasic
                access6_idx = access_idx_arr(6);
                accessPotential_6 = potential_arr(access6_idx);
                accessVoltage_arr(6) = accessPotential_6 - drivingPotential_arr(3);
                fprintf('6...');
            else
                access4_idx = access_idx_arr(4);
                accessPotential_4 = potential_arr(access4_idx);
                accessVoltage_arr(4) = accessPotential_4 - drivingPotential_arr(2);
                fprintf('4...');
            end
        end
        

        fprintf('Resistance...');
        updateWaitbar(File,'Calculating access resistance...');
        accessVoltage_mag_arr = abs(accessVoltage_arr);
        amplitude_A = amplitude_mag_arr * 1e-6;                         % uA to A
        try
            accessResistance_Ohm_arr = accessVoltage_mag_arr ./ amplitude_A;% Ohm
        catch
            display(accessVoltage_mag_arr);
            display(amplitude_A);
        end
        accessResistance_arr = accessResistance_Ohm_arr / 1e3;          % Ohm to kOhm

        [endTime,unit] = getEndTime(startTime);
        fprintf('OK \t\t(%.2f %s)\n',endTime,unit);

    end
end

%% Driving Calculation
fprintf('Calculating driving voltage...');
updateWaitbar(File,'Calculating driving voltage...');
startTime = tic;
for phaseNum = 1:numOfPhases
    fprintf('%d...',phaseNum);
    driving_idx = driving_idx_arr(phaseNum);
    value = voltage_arr(driving_idx);
    switch phaseNum
        case 1
            drivingVoltage_arr(phaseNum) = abs(prePulseVoltage - value);
        case 2
            if interphaseDelay > 0
                if isempty(endInterphase1_idx)
                    interphase1_arr = voltage_arr(interphase1_tf);
                    interphase1_mag = smooth(abs(interphase1_arr));
                    [~,endInterphase1_idx] = min(interphase1_mag);
                    endInterphase1 = interphase1_arr(endInterphase1_idx);
                else
                    endInterphase1 = potential_arr(endInterphase1_idx);
                end
                drivingVoltage_arr(phaseNum) = abs(endInterphase1 - value);
            else
                drivingVoltage_arr(phaseNum) = abs(prePulseVoltage - value);
            end
        case 3
            if interphaseDelay > 0
                if isempty(endInterphase2_idx)
                    interphase2_arr = voltage_arr(interphase2_tf);
                    interphase2_mag = smooth(abs(interphase2_arr));
                    [~,endInterphase2_idx] = min(interphase2_mag);
                    endInterphase2 = interphase2_arr(endInterphase2_idx);
                else
                    endInterphase2 = potential_arr(endInterphase2_idx);
                end
                drivingVoltage_arr(phaseNum) = abs(endInterphase2 - value);
            else
                drivingVoltage_arr(phaseNum) = abs(prePulseVoltage - value);
            end
    end
end
[endTime,unit] = getEndTime(startTime);
fprintf('OK \t\t(%.2f %s)\n',endTime,unit);

%% Output
varargout{1} = drivingVoltage_arr;
varargout{2} = driving_idx_arr;
varargout{3} = accessVoltage_arr;
varargout{4} = accessResistance_arr;
varargout{5} = access_idx_arr;
varargout{6} = isCancel;

end