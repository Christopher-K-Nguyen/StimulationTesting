function [File,buttonHandle] = getWaveformData2(File)
%% Constants
% Conversion
N_TO_MICRO = 1e6;
MILLI_TO_N = 1e-3;

% Waveform check
% BAD_THRESH = 1.2;   % threshold for bad
COMPLIANCE_THRESH = 9;  % threshold for compliance

% Display
VAR_NAMES = {'Phase 1','Phase 2'};
ROW_NAMES = {...
    'Vacc (V)','Racc (kOhm)', ...
    'Vdrive (V)','Vdiff (V)', ...
    'Emax pw (V)','Emax depol (V)'};

%% Variables
startCapture = tic;
subjectSelect = File.Subject;
isAnimal = contains2(subjectSelect,'A') && ~contains2(subjectSelect,'PA04');
if isAnimal
    capCheck = 0.98;
else
    capCheck = 0.992;
end
channel_arr = [File.Data.Channel];
channelNum = length(channel_arr);
capture_arr = [File.Data(channelNum).Capture(:).Index];
captureNum = length(capture_arr);
amplitudeList = [File.Data(channelNum).Capture.Amplitude];
idx_shift = 5;
if captureNum > idx_shift
    check_idx = captureNum - idx_shift;
    amplitudeList_check = amplitudeList(check_idx:captureNum);
    [~,modeCount] = mode(amplitudeList_check);
    isAmplitudeRepeat = any(modeCount > 1);
else
    isAmplitudeRepeat = false;
end
surfaceArea = File.Data(channelNum).SurfaceArea;

% Potential
refShift = File.ReferenceElectrode.Shift;
lowerLimit = File.ReferenceElectrode.LowerPotential;
upperLimit = File.ReferenceElectrode.UpperPotential;

% Scope
oscilloscope = File.Oscilloscope.Object;
trigSource = File.Oscilloscope.Trigger.Source;
isExtTrig = contains2(trigSource,'EXT');
digitalDelay = File.Stimulator.DigitalDelay;
stimType = File.Parameters.Type;
isMultiTest = contains2(stimType,'MULTI');
external = File.Parameters.External;
hasExternal = ~isempty(external);
dataLength = File.Oscilloscope.Settings.DataLength;
isRateTest = contains2(stimType,'RATE');
isTargetMax = contains2(stimType,'MAX') || isRateTest;
hasCurrent = false;
isAlwaysGetCurrent = false;

% Pattern
% stimRate = File.Parameters.StimulationRate;
amplitude1 = File.Data(channelNum).Capture(captureNum).Amplitude;
amplitude1_sign = sign(amplitude1);
% if amplitude1 <= 0
%     potentialLimit = lowerLimit;
% else
%     potentialLimit = upperLimit;
% end
% 
% if isAnimal
%     if refShift > 0
%         potentialCheck = 0.4;
%     elseif isRateTest
%         potentialCheck = 0.3;
%     else
%         potentialCheck = 0.2;
%     end
% else
%     if isRateTest
%         potentialCheck = 0.4;
%     else
%         potentialCheck = 0.35;
%     end
% end
% if captureNum == 1
%     %     accessVoltage_check = 0.01;
%     badThresh = abs(potentialLimit) + potentialCheck;   % threshold for bad
% else
%     %     accessVoltage_check = 0.02;
%     badThresh = abs(potentialLimit) + potentialCheck * 1.25;   % threshold for bad
% end
% if captureNum == 1
%     %     accessVoltage_check = 0.01;
%     badThresh = abs(potentialLimit) + potentialCheck;   % threshold for bad
% else
%     if isAnimal
%         badThresh = 2;
%     else
%         badThresh = 3;
%     end
% end

if isAnimal
    if captureNum > 1
        badThresh = 2;
    else
        badThresh = 1.2;
    end
else
    if captureNum > 1
        badThresh = 3;
    else
        badThresh = 1.2;
    end
end
amplitude_mag = abs(amplitude1);
phaseWidth1 = File.Parameters.PhaseWidth1;        % first phase pulse width
phaseWidth2 = File.Parameters.PhaseWidth2;        % second phase pulse width
interphaseDelay = File.Parameters.InterphaseDelay;% interphase delay
if phaseWidth1 >= 100
    maxAttempts = 4;
    accessVoltage1_idx_old = 10;
    accessVoltage2_idx_old = 11;
    cutoff_idx = 25;
else
    maxAttempts = 5;
    accessVoltage1_idx_old = 5;
    accessVoltage2_idx_old = 10;%% Constants
    cutoff_idx = 10;
end

if interphaseDelay >= 50
    depolTime = 12;
elseif interphaseDelay == 0
    depolTime = 0;
else
    depolTime = interphaseDelay / 2;
end

% Phase 1
potentialExcursion1_time = phaseWidth1 + depolTime;
% Interphase Delay
afterInterphaseDelay = phaseWidth1 + interphaseDelay;
% Phase 2
afterPhase2 = phaseWidth1 + phaseWidth2 + interphaseDelay;
potentialExcursion2_time = afterPhase2 + depolTime;

% Charge
isMaxCurrent = amplitude_mag >= 1e3;
chargePhase = File.Data(channelNum).Capture(captureNum).ChargePhase;       	    % charge per phase
chargeInjection = File.Data(channelNum).Capture(captureNum).ChargeInjection;        % charge injection

% Plot label
channelTitle = sprintf('Channel %d',channelNum);
amplitude_use = sprintf('{\\iti}_{mag} = %.2f \\muA',amplitude_mag);
chargePhase_use = sprintf('{\\itQ}_{ph} = %.2f nC/ph',chargePhase);
chargeInjection_use = sprintf('{\\itQ}_{inj} = %.3f mC/cm^2',chargeInjection);
subtitle_charge = [amplitude_use '; ' chargePhase_use '; ' chargeInjection_use];
% if chargePhase > 0
%     subtitle_charge = sprintf( ...
%         '{\\iti}_{mag} = %.2f \\muA; {\\itQ}_{ph} = %.2f nC/ph; {\\itQ}_{inj} = %.3f mC/cm^2', ...
%         amplitude_mag, ...
%         chargePhase, ...
%         chargeInjection);
% else
%     subtitle_charge = sprintf( ...
%         '{\\iti}_{mag} = 0 \\muA; {\\itQ}_{ph} = 0 nC/ph; {\\itQ}_{ph} = 0 mC/cm^2');
% end

% Initialization
zeroCurrent_idx = 12;
% Time
diffTime = 0;
% Waveform
capacitance = [];
isWaveformNorm = false;
isVoltageNorm = false;
isCurrentNorm = false;
status = 'Good';
limitStatus = '';
try
    isTooLong = File.Data(channelNum).Capture(captureNum).Status.TooLong;
catch
    isTooLong = false;
end
isGood = true;
isBad = false;
isVoltageSafe = true;
isLimitReached = 0;
isAtVoltageCompliance = false;
isCurrentNeeded = false;
isQuit = false;
isVoltageBad = false;
isAnteVoltageBad = false;
isPostVoltageBad = false;
isCurrentBad = false;
isAnteCurrentBad = false;
isPostCurrentBad = false;
badMsg = '';
attemptNum = 1;
potentialExcursion1 = 0;
potentialExcursion2 = 0;
accessVoltage1 = 0;
accessVoltage2 = 0;
drivingVoltage1 = 0;
drivingVoltage2_plot = 0;
buttonHandle = gobjects(1);

%% Time
[File,time_raw] = getTime2(File);
time = time_raw * N_TO_MICRO;
time0_idx = find(time >= 0,1);
fprintf('\tProcessing time...');
startTime = tic;
diffTime = File.Oscilloscope.Settings.Interval * N_TO_MICRO;
if phaseWidth1 < 100
    idx_shift = 0;
    if isAnimal
        idx_shift_more = 12;
    else
        idx_shift_more = 10;
    end
else
    idx_shift = 0;
    if isAnimal %#ok<IFBDUP>
        idx_shift_more = 5;
    else
        idx_shift_more = 5;
    end
end
if isExtTrig
    trig_idx_shift = floor(digitalDelay / diffTime);
    if phaseWidth1 < 100
        adjust_idx = - 10;
        prePhaseTime2 = 2;
        zeroCurrent_idx = zeroCurrent_idx - adjust_idx;
        accessVoltage1_idx_old = accessVoltage1_idx_old - 1;
    else
        if isAnimal
            adjust_idx = 30;
        else
            adjust_idx = 0;
        end
        prePhaseTime2 = 3;
    end
    accessVoltage1_idx_old = accessVoltage1_idx_old + trig_idx_shift - adjust_idx;
    accessVoltage2_idx_old = accessVoltage2_idx_old + trig_idx_shift - adjust_idx;
else
    trig_idx_shift = 0;
    prePhaseTime2 = 3.5;
end
endInterphase_shift_idx = ceil(prePhaseTime2 / diffTime);
drivingVoltage1_idx = find(time >= phaseWidth1,1);
drivingVoltage2_idx = find(time >= afterPhase2,1);
endPhase1_idx_old = drivingVoltage1_idx + zeroCurrent_idx;
endPhase2_idx_old = drivingVoltage2_idx + zeroCurrent_idx;
if interphaseDelay > 0
    potentialExcursion1_idx = find(time >= potentialExcursion1_time,1);   % index of first max potential
    potentialExcursion2_idx = find(time >= potentialExcursion2_time,1);   % index of second max potential
else
    potentialExcursion1_idx = endPhase1_idx_old;
    potentialExcursion2_idx = endPhase2_idx_old;
    potentialExcursion1_time = time(potentialExcursion1_idx);
    potentialExcursion2_time = time(potentialExcursion2_idx);
end
phase1_tf = time >= 0 & time <= phaseWidth1;
phase1_idx = find(phase1_tf);
phase1_len = length(phase1_idx);
accessDerivative1_check = phase1_idx(1) + 0.15 * phase1_len;
accessVoltage1_idx_old = accessVoltage1_idx_old + phase1_idx(1) - 1;
phase2_tf = time >= afterInterphaseDelay & time <= afterPhase2;
phase2_idx = find(phase2_tf);
phase2_len = length(phase2_idx);
accessDerivative2_check = phase2_idx(1) + 0.15 * phase2_len;
accessVoltage2_idx_old = accessVoltage2_idx_old + phase2_idx(1) - 1;
prePulse_idx = time < 0;
File.Data(channelNum).Capture(captureNum).Time = time;
[endTime,unit] = getEndTime(startTime);
fprintf('OK (%.2f %s)\n',endTime,unit);

%% Waveform
while ~isWaveformNorm
    %% Voltage
    if ~isVoltageNorm
        scopeChannel = 'CH1';
        [voltage,~] = getWaveform2(File,scopeChannel);
        File.Data(channelNum).Capture(captureNum).Voltage = voltage; % voltage
        if isMultiTest
            scopeChannel = 'CH3';
            [working,~] = getWaveform2(File,scopeChannel);
            File.Data(channelNum).Capture(captureNum).Working = working; % voltage
            scopeChannel = 'CH4';
            [counter,~] = getWaveform2(File,scopeChannel);
            File.Data(channelNum).Capture(captureNum).Counter = counter; % voltage
            testVoltage = working;
        else
            testVoltage = voltage;
        end
    end
    testVoltage_mag = abs(testVoltage);
    isVoltageEmpty = all(testVoltage_mag < 0.005);

    % Access Voltage Index
    fprintf('Getting voltage indices...');
    startTime = tic;
    voltage_filt = lowpass(testVoltage,0.0001);
    voltage_diff = diff(voltage_filt);
    time_diff = diff(time);
    derivative_raw = voltage_diff ./ time_diff;
    derivative = lowpass(derivative_raw,0.0001);
    %         voltage_diff2 = diff(voltage_diff);
    %         time_diff2 = diff(time_diff);
    %         derivative2_raw = voltage_diff2 ./ time_diff2;
    %         derivative2 = smooth(derivative2_raw);
    peakMax = max(derivative);
    isAccessDerivative1Good = false;
    isAccessDerivative2Good = false;
    isEndDerivative1Good = false;
    isEndDerivative2Good = false;
    accessDerivative1_idx = [];
    accessDerivative2_idx = [];
    endDerivative1_idx = [];
    endDerivative2_idx = [];
    accessVoltage1_idx = [];
    accessVoltage2_idx = [];
    endPhase1_idx = [];
    endPhase2_idx = [];
    peakPos_thresh = peakMax * 0.8;
    peakNeg_thresh = peakMax * 0.8;
    accessCheckTime = tic;
    while isempty(accessVoltage1_idx) || isempty(accessVoltage2_idx) ...
            || isempty(endPhase1_idx) || isempty(endPhase2_idx)
        [~,peaksPos_idx] = findpeaks(derivative,'MinPeakHeight',peakPos_thresh);
        [~,peaksNeg_idx] = findpeaks(-derivative,'MinPeakHeight',peakNeg_thresh);
        switch amplitude1_sign
            case -1
                accessDerivative1_idx = min(peaksNeg_idx);
                accessDerivative2_idx = max(peaksPos_idx);
                endDerivative1_idx = min(peaksPos_idx);
                endDerivative2_idx = max(peaksNeg_idx);
            case 1
                accessDerivative1_idx = min(peaksPos_idx);
                accessDerivative2_idx = max(peaksNeg_idx);
                endDerivative1_idx = min(peaksNeg_idx);
                endDerivative2_idx = max(peaksPos_idx);
        end

        % Acess Voltage 1
        if isempty(accessDerivative1_idx)...
                || accessDerivative1_idx < time0_idx ...
                || accessDerivative1_idx > accessDerivative1_check
            accessDerivative1_idx = [];
            switch amplitude1_sign
                case -1
                    peakNeg_thresh = peakNeg_thresh * 0.9;
                case 1
                    peakPos_thresh = peakPos_thresh * 0.9;
            end
        else
            accessVoltage1_idx = accessDerivative1_idx;
            isAccessDerivative1Good = true;
        end

        % Access Voltage 2
        if isempty(accessDerivative2_idx) ...
                || accessDerivative2_idx < potentialExcursion1_idx ...
                || accessDerivative2_idx > accessDerivative2_check
            accessDerivative2_idx = [];
            switch amplitude1_sign
                case -1
                    peakPos_thresh = peakPos_thresh * 0.9;
                case 1
                    peakNeg_thresh = peakNeg_thresh * 0.9;
            end
        else
            accessVoltage2_idx = accessDerivative2_idx;
            isAccessDerivative2Good = true;
        end

        % End Phase 1
        if isempty(endDerivative1_idx)...
                || endDerivative1_idx < accessDerivative1_check ...
                || endDerivative1_idx > potentialExcursion1_idx ...
                || endDerivative1_idx < endPhase1_idx_old * 0.9
            endDerivative1_idx = [];
            switch amplitude1_sign
                case -1
                    peakNeg_thresh = peakNeg_thresh * 0.9;
                case 1
                    peakPos_thresh = peakPos_thresh * 0.9;
            end
        else
            endPhase1_idx = endDerivative1_idx;
            isEndDerivative1Good = true;
        end

        % End Phase 2
        if isempty(endDerivative2_idx) ...
                || endDerivative2_idx > potentialExcursion2_idx ...
                || endDerivative2_idx < accessDerivative2_check ...
                || endDerivative2_idx < endPhase2_idx_old * 0.9
            endDerivative2_idx = [];
            switch amplitude1_sign
                case -1
                    peakPos_thresh = peakPos_thresh * 0.9;
                case 1
                    peakNeg_thresh = peakNeg_thresh * 0.9;
            end
        else
            endPhase2_idx = endDerivative2_idx;
            isEndDerivative2Good = true;
        end

        if toc(accessCheckTime) > 1
            if isempty(accessVoltage1_idx)
                accessVoltage1_idx = accessVoltage1_idx_old;
                isAccessDerivative1Good = false;
            end
            if isempty(accessVoltage2_idx)
                accessVoltage2_idx = accessVoltage2_idx_old;
                isAccessDerivative2Good = false;
            end
            if isempty(endDerivative1_idx)
                endPhase1_idx = endPhase1_idx_old;
                isEndDerivative1Good = false;
            end
            if isempty(endDerivative2_idx)
                endPhase2_idx = endPhase2_idx_old;
                isEndDerivative2Good = false;
            end
        end
    end
    % Shift
    if isAccessDerivative1Good
        accessVoltage1_idx = accessVoltage1_idx + idx_shift + idx_shift_more;
        endInterphase_shift_idx = idx_shift_more + 16;
    end
    if isAccessDerivative2Good
        accessVoltage2_idx = accessVoltage2_idx + idx_shift + idx_shift_more;
    end
    if isEndDerivative1Good
        endPhase1_idx = endPhase1_idx + idx_shift + idx_shift_more;
    end
    if isEndDerivative2Good
        endPhase2_idx = endPhase2_idx + idx_shift + idx_shift_more;
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('OK (%.2f %s)\n',endTime,unit);

    fprintf('Getting values of interest...');
    startTime = tic;
    % Phase 1
    prePulseVoltage = mean(testVoltage(prePulse_idx));
    %         [~,~,r2Exp_1] = getExpReg(phase1_time,phase1_voltage);
    % access voltage
    accessVoltage1_time = time(accessVoltage1_idx);
    accessVoltage1_plot = testVoltage(accessVoltage1_idx);
    accessVoltage1 = abs(accessVoltage1_plot - prePulseVoltage);
    accessResistance1 = accessVoltage1 / amplitude_mag * 1e3;
    % driving voltage
    testVoltage_half1 = testVoltage(1:potentialExcursion1_idx);
    [voltageMin,voltageMin_idx]  = min(testVoltage_half1);
    drivingVoltage1_plot = voltageMin;
    drivingVoltage1 = abs(drivingVoltage1_plot - prePulseVoltage);
    %         drivingVoltage1 = abs(voltageMin);% driving voltage
    if voltageMin_idx < drivingVoltage1_idx
        isDrivingVoltage1Good = false;
        drivingVoltage1_treshCheck = abs(0.05 * drivingVoltage1_plot);
        drivingVoltage1_check = drivingVoltage1_plot;
        try
            drivingCheckTime = tic;
            while ~isDrivingVoltage1Good
                idx_arr = [voltageMin_idx drivingVoltage1_check];
                drivingVoltage1_idx_check = floor(mean(idx_arr));
                drivingVoltage1_check = testVoltage(drivingVoltage1_idx_check);
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
    drivingVoltage1_time = time(drivingVoltage1_idx);
    drivingVoltage1_plot = testVoltage(drivingVoltage1_idx);
    drivingVoltage1 = abs(drivingVoltage1_plot - prePulseVoltage);
    voltageDiff1 = drivingVoltage1 - accessVoltage1;
    % potential excursion
    potentialExcursion1_zero = testVoltage(endPhase1_idx);
    potentialExcursion1_zero_time = time(endPhase1_idx);
    potentialExcursion1 = testVoltage(potentialExcursion1_idx);  	% max cathodal potential

    % Phase 2
    %         [~,~,r2Exp_2] = getExpReg(phase2_time,phase2_voltage);
    endInterphase_idx = accessVoltage2_idx - endInterphase_shift_idx;
    endInterphase_voltage = testVoltage(endInterphase_idx);
    % access voltage
    accessVoltage2_time = time(accessVoltage2_idx);
    accessVoltage2_plot = testVoltage(accessVoltage2_idx);
    accessVoltage2 = abs(accessVoltage2_plot - endInterphase_voltage);
    accessResistance2 = accessVoltage2 / amplitude_mag * 1e3;
    % driving voltage
    testVoltage_half2 = testVoltage(potentialExcursion1_idx:dataLength);
    [voltageMax,~] = max(testVoltage_half2);
    voltageMax_idx = find(testVoltage == voltageMax,1);
    drivingVoltage2_plot = abs(voltageMax);% driving voltage
    drivingVoltage2 = abs(drivingVoltage2_plot - endInterphase_voltage);
    if voltageMax_idx < drivingVoltage2_idx
        drivingVoltage2_treshCheck = abs(0.05 * drivingVoltage2_plot);
        drivingVoltage2_check = drivingVoltage2_plot;
        isDrivingVoltage2Good = false;
        try
            drivingCheckTime = tic;
            while ~isDrivingVoltage2Good
                idx_arr = [voltageMax_idx drivingVoltage2_check];
                drivingVoltage2_idx_check = floor(mean(idx_arr));
                drivingVoltage2_check = testVoltage(drivingVoltage2_idx_check);
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
    drivingVoltage2_time = time(drivingVoltage2_idx);
    drivingVoltage2_plot = abs(testVoltage(drivingVoltage2_idx));% driving voltage
    drivingVoltage2 = abs(drivingVoltage2_plot - endInterphase_voltage);
    % voltage difference
    voltageDiff2 = drivingVoltage2 - accessVoltage2;
    % potential excursion
    potentialExcursion2_zero = testVoltage(endPhase2_idx);
    potentialExcursion2_zero_time = time(endPhase2_idx);
    potentialExcursion2 = testVoltage(potentialExcursion2_idx);
    [endTime,unit] = getEndTime(startTime);
    fprintf('OK (%.2f %s)\n',endTime,unit);

    % Check
    [isAnteVoltageBad,isPostVoltageBad] = checkWaveformEnds(File,scopeChannel);
    isVoltageBad = isAnteVoltageBad || isPostVoltageBad || isVoltageEmpty;
    isVoltageNorm =  ~isVoltageBad;

    % Store
    % potential excursion
    potentialExcursion_arr = [potentialExcursion1;potentialExcursion2];
    File.Data(channelNum).Capture(captureNum).PotentialExcursion = potentialExcursion_arr;% potential excursion
    % access voltage
    accessVoltage_arr = [accessVoltage1;accessVoltage2];
    File.Data(channelNum).Capture(captureNum).AccessVoltage = accessVoltage_arr;    % access voltage
    % driving voltage
    drivingVoltage_arr = [drivingVoltage1;drivingVoltage2_plot];
    File.Data(channelNum).Capture(captureNum).DrivingVoltage = drivingVoltage_arr;  % driving voltage
    % access resistance
    accessResistance_arr = [accessResistance1;accessResistance2];
    File.Data(channelNum).Capture(captureNum).AccessResistance = accessResistance_arr;

    % Check if broken
    %         isLowerLimitBroken = drivingVoltage1 < -COMPLIANCE_THRESH; % lower limit
    %         isUpperLimitBroken = drivingVoltage2 > COMPLIANCE_THRESH;  % upper limit
    %         isAtVoltageCompliance = isLowerLimitBroken || isUpperLimitBroken;
    isAtVoltageCompliance = any(testVoltage_mag > COMPLIANCE_THRESH);
    if isAtVoltageCompliance
        status = 'Voltage compliance reached';
    else
        % Check if bad
        [isLimitReached,status] = checkPotentialExcursion(File);
        %             isLowerLimitBad = potentialExcursion1 < -badThresh; % lower limit
        %             isUpperLimitBad = potentialExcursion2 > badThresh;  % upper limit
        %             isVoltageSafe = ~(isUpperLimitBad || isLowerLimitBad);
        isVoltageSafe = ~(any(abs(potentialExcursion_arr) > badThresh));
        if ~isVoltageSafe
            status = 'Voltage is unsafe';
        end
    end

    % Capacitance
    fprintf('Checking capacitance...');
    startTime = tic;
    if isAtVoltageCompliance || ~isVoltageSafe
        capEnd_idx = drivingVoltage1_idx - cutoff_idx;
    else
        phase1 = accessVoltage1_idx:drivingVoltage1_idx;
        phase1_len = length(phase1);
        phase1_cutoff = ceil(phase1_len * 0.05);
        capEnd_idx = drivingVoltage1_idx - phase1_cutoff;
    end
    cap_idx = accessVoltage1_idx:capEnd_idx;
    cap_time = time(cap_idx);
    cap_voltage = testVoltage(cap_idx);
    [slope,intercept,r2] = getLinReg(cap_time,cap_voltage);
    if r2 > capCheck || isAtVoltageCompliance
        capacitance = amplitude_mag / abs(slope);
        cap_line = slope * cap_time + intercept;
    elseif isAccessDerivative1Good
        cap_idx = accessVoltage1_idx:drivingVoltage1_idx;
        cap_time = time(cap_idx);
        cap_voltage = testVoltage(cap_idx);
        [slope,intercept,r2] = getLinReg(cap_time,cap_voltage);
        if r2 > capCheck
            capacitance = amplitude_mag / abs(slope);
            cap_line = slope * cap_time + intercept;
        end
    end
    [endTime,unit] = getEndTime(startTime);
    if any(capacitance)
        fprintf('FOUND (%.2f %s)\n',endTime,unit);
    else
        fprintf('NONE (%.2f %s)\n',endTime,unit);
    end

    % Check if broken
    isBad = isAtVoltageCompliance || ~isVoltageSafe;
    isGood = ~isBad;
    File.Data(channelNum).Capture(captureNum).Capacitance = capacitance;
    File.Data(channelNum).Capture(captureNum).Status.Good = isGood;
    File.Data(channelNum).Capture(captureNum).Status.PotentialLimit = isLimitReached;
    File.Data(channelNum).Capture(captureNum).Status.VoltageSafety = isVoltageSafe;
    File.Data(channelNum).Capture(captureNum).Status.VoltageCompliance = isAtVoltageCompliance;

    % Values
    % time
    voltageValue_arr_time = [...
        accessVoltage1_time,drivingVoltage1_time, ...
        accessVoltage2_time,drivingVoltage2_time];
    File.Data(channelNum).Capture(captureNum).Figure.VoltageValues.Time = voltageValue_arr_time;
    % voltage
    voltageValue_arr_voltage = [...
        accessVoltage1,drivingVoltage1, ...
        accessVoltage2,drivingVoltage2];
    File.Data(channelNum).Capture(captureNum).Figure.VoltageValues.Voltage = voltageValue_arr_voltage;
    % plot
    voltagePlot_arr = [...
        accessVoltage1_plot;drivingVoltage1_plot;...
        accessVoltage2_plot;drivingVoltage2_plot];
    File.Data(channelNum).Capture(captureNum).Figure.VoltageValues.Plot = voltagePlot_arr;
    % Difference
    voltageDiff_arr = [voltageDiff1;voltageDiff2];
    File.Data(channelNum).Capture(captureNum).Figure.VoltageValues.Difference = voltageDiff_arr;
    % Potential Excursion
    % time
    potentialExcursion_arr_time = [...
        potentialExcursion1_zero_time,potentialExcursion1_time, ...
        potentialExcursion2_zero_time,potentialExcursion2_time];
    File.Data(channelNum).Capture(captureNum).Figure.PotentialExcursion.Time = potentialExcursion_arr_time;
    % voltage
    potentialExcursion_arr_voltage = [...
        potentialExcursion1_zero,potentialExcursion1, ...
        potentialExcursion2_zero,potentialExcursion2];
    File.Data(channelNum).Capture(captureNum).Figure.PotentialExcursion.Voltage = potentialExcursion_arr_voltage;

    % Display
    if ~isAtVoltageCompliance
        phase1 = [...
            accessVoltage1;accessResistance1;drivingVoltage1;voltageDiff1;...
            potentialExcursion1_zero;potentialExcursion1];
        phase2 = [...
            accessVoltage2;accessResistance2;drivingVoltage2;voltageDiff2;...
            potentialExcursion2_zero;potentialExcursion2];
        phase1_round = round(phase1,3,'significant');
        phase2_round = round(phase2,3,'significant');
        displayTable = table(phase1_round,phase2_round, ...
            'VariableNames',VAR_NAMES, ...
            'RowNames',ROW_NAMES);
        disp(displayTable);

        % % phase 1
        % fprintf('\tVacc = %.3f V\n',accessVoltage1);
        % fprintf('\tRacc = %.3f V\n',accessResistance1);
        % fprintf('\tVdrive = %.3f V\n',drivingVoltage1);
        % fprintf('\tVdiff = %.3f V\n',voltageDiff1);
        % fprintf('\tEmc(pw) = %.3f V\n',potentialExcursion1_zero);
        % fprintf('\tEmc(depol) = %.3f V\n',potentialExcursion1);
        % % phase 2
        % fprintf('\tVacc = %.3f V\n',accessVoltage2);
        % fprintf('\tRacc = %.3f V\n',accessResistance2);
        % fprintf('\tVdrive = %.3f V\n',drivingVoltage2_plot);
        % fprintf('\tVdiff = %.3f V\n',voltageDiff2);
        % fprintf('\tEma(pw) = %.3f V\n',potentialExcursion2_zero);
        % fprintf('\tEma(depol) = %.3f V\n',potentialExcursion2);
    end
    if any(capacitance)
        capacitance_use = addCommas(capacitance);
        fprintf('\tC = %s uF\n',capacitance_use);
    end

    % Plot
    buttonHandle = getAcutePlot3(File);
    fig = figure(channelNum);
    title(channelTitle);
    subtitle(subtitle_charge);

    % Plot title
    figure(fig);
    if any(capacitance)
        hold on;
        if hasCurrent
            yyaxis left;
        end
        plot(cap_time,cap_line,'k:','LineWidth',2,'HandleVisibility','off');
    end
    if isVoltageBad
        waveformStatus = 'Voltage NOT Normal';
        isVoltageBad = false;
    elseif isTooLong
        waveformStatus = 'Took Too Long';
    elseif isAtVoltageCompliance
        waveformStatus = 'Voltage Compliance';
    elseif ~isVoltageSafe
        waveformStatus = 'NOT Safe';
    elseif any(isLimitReached)
        waveformStatus = 'Limit Reached';
    elseif isMaxCurrent
        waveformStatus = 'Max Current';
    elseif isAmplitudeRepeat
        waveformStatus = 'NOT Enough Precision';
    else
        waveformStatus = '';
    end
    if isempty(waveformStatus)
        channelTitle = sprintf('Channel %d',channelNum);
    else
        channelTitle = sprintf('Channel %d (%s)',channelNum,waveformStatus);
    end
    title(channelTitle);
    subtitle(subtitle_charge);

    %% Current
    isAtLimits = any(isLimitReached) || isBad;
    isAtTarget = ~isTargetMax || isMaxCurrent;
    isTimingBad = isTooLong || (attemptNum > maxAttempts) || isAmplitudeRepeat;
    isCurrentNeeded = (isAtLimits || isAtTarget || isTimingBad);
    if isCurrentNeeded || isAlwaysGetCurrent
        scopeChannel = 'CH2';
        if ~isCurrentNorm
            [current,~] = getWaveform2(File,scopeChannel);
            current_mag = abs(current);
            isCurrentEmpty = all(current_mag < 0.2);
            currentDensity = getCurrentDensity(current,surfaceArea) * MILLI_TO_N;
            hasCurrent = true;
            File.Data(channelNum).Capture(captureNum).Current = current; % current
            File.Data(channelNum).Capture(captureNum).CurrentDensity = currentDensity; % current density
        end
        % Check
        [isAnteCurrentBad,isPostCurrentBad] = checkWaveformEnds(File,scopeChannel);
        isCurrentBad = isAnteCurrentBad || isPostCurrentBad;
        isCurrentNorm = ~isCurrentBad && ~isCurrentEmpty;

        % Plot
        buttonHandle = getAcutePlot3(File);
        figure(fig);
        title(channelTitle);
        subtitle(subtitle_charge);
        if any(capacitance)
            hold on;
            yyaxis left;
            plot(cap_time,cap_line,'k:','LineWidth',2,'HandleVisibility','off');
        end

        % Plot title
        figure(fig);
        if isVoltageBad && isCurrentBad
            waveformStatus = 'NOT Normal';
%             isVoltageBad = false;
%             isCurrentNorm = false;
        elseif isCurrentBad
            waveformStatus = 'Current NOT Normal';
%             isCurrentNorm = false;
        end
        
        if isempty(waveformStatus)
            channelTitle = sprintf('Channel %d',channelNum);
        else
            channelTitle = sprintf('Channel %d (%s)',channelNum,waveformStatus);
        end
        title(channelTitle);
        subtitle(subtitle_charge);
    else
        isCurrentNorm = true;
    end

    %% Check Waveform
    isWaveformNorm = isVoltageNorm && isCurrentNorm;
    startTime = tic;
    if ~isWaveformNorm
        if isVoltageBad && isCurrentBad
            if isAnteVoltageBad && isAnteCurrentBad
                badMsg = 'Pre-pulse voltage and current';
            elseif isPostVoltageBad && isPostCurrentBad
                badMsg = 'Post-pulse voltage and current';
            elseif isAnteVoltageBad && isAnteCurrentBad...
                    && isPostVoltageBad && isPostCurrentBad
                badMsg = 'Pre/post-pulse voltage and current';
            elseif isAnteVoltageBad && isPostCurrentBad
                badMsg = 'Pre-pulse voltage and post-pulse current';
            elseif isPostVoltageBad && isAnteCurrentBad
                badMsg = 'post-pulse voltage and pre-pulse current';
            elseif isAnteVoltageBad && isAnteCurrentBad && isPostVoltageBad
                badMsg = 'Pre/post-pulse voltage and pre-pulse current';
            elseif isAnteVoltageBad && isAnteCurrentBad && isPostCurrentBad
                badMsg = 'Pre-pulse voltage and pre/post-pulse current ';
            elseif isPostVoltageBad && isPostCurrentBad && isAnteVoltageBad
                badMsg = 'Pre/post-pulse voltage and post-pulse current';
            elseif isPostVoltageBad && isPostCurrentBad && isAnteCurrentBad
                badMsg = 'Post-pulse voltage and pre/post-pulse current';
            end
        elseif isVoltageBad
            if isAnteVoltageBad && isPostVoltageBad
                badMsg = 'Pre/post-pulse voltage';
            elseif isAnteVoltageBad
                badMsg = 'Pre-pulse voltage';
            elseif isPostVoltageBad
                badMsg = 'Post-pulse voltage';
            elseif isVoltageEmpty
                badMsg = 'Voltage transient';
            end
        elseif isCurrentBad
            if isAnteCurrentBad && isPostCurrentBad
                badMsg = 'Pre/post-pulse current';
            elseif isAnteCurrentBad
                badMsg = 'Pre-pulse current';
            elseif isPostCurrentBad
                badMsg = 'Post-pulse current';
            end
        end

        % Attempts
        if attemptNum > maxAttempts
            tooManyBad = lower(badMsg);
            fprintf('Too many attempts to normalize %s...',tooManyBad);
            %             isGood = false;
            isWaveformNorm = true;
            [endTime,unit] = getEndTime(startTime);
            fprintf('moving on (%.2f %s)\n',endTime,unit);
        else
            attemptNum = attemptNum + 1;  % increment attempt number
            fprintf('%s not normalized...',badMsg);
            fprintf(oscilloscope,'ACQuire:STAte RUN');
            fprintf('trying again.\n');
            %             [channelStim,~] = PS_GetMonitorChannel(1);
            %             fprintf('\t');
            %             stopStimAllChannels(1);
            %             pause(3);
            %             fprintf('\t');
            %             setStimRate(1,channelStim,stimRate);
            %             fprintf('\t');
            %             setCurrentScale(File,amplitude1);
            %             startStimChannel2(1,channelStim);
            %             pause(1);
            %             setTriggerLevel2(File);
        end
    else
        if hasCurrent
            fprintf('Voltage and current normalized...');
        else
            fprintf('Voltage normalized...');
        end
        isWaveformNorm = true;
        [endTime,unit] = getEndTime(startTime);
        fprintf('moving on (%.2f %s)\n',endTime,unit);
    end

    % Stop by button handle
    if ~ishandle(buttonHandle)
        isQuit = true;
        fprintf('Experiment canceled by user...');
        break;
    end

    if isBad && hasCurrent
        break;
    end
end
% if isBad || hasCurrent
%     stopStimAllChannels(1);
% end

%% Store
% if ~hasCurrent
%     File.Data(channelNum).Capture(captureNum).Current = []; % current
%     File.Data(channelNum).Capture(captureNum).CurrentDensity = []; % current density
% end

if ~isTooLong
    %     if ~isempty(limitStatus)
    %         status = limitStatus;
    %     end
    File.Data(channelNum).Capture(captureNum).Status.Description = status;
end
File.Data(channelNum).Capture(captureNum).Status.Quit = isQuit;
[endCapture,unit] = getEndTime(startCapture);
fprintf('Capture Time: %.2f %s\n',endCapture,unit);

end