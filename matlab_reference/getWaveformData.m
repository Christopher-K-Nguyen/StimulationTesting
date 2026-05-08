function varargout = getWaveformData(File)
% try
%% Constants
% Waveform check
% BAD_THRESH = 1.2;   % threshold for bad
COMPLIANCE_THRESH = 9;  % threshold for compliance


%% Variables
startCapture = tic;
numOfDevices = length(File.Oscilloscope);
configID = File.Parameters.Configuration.ID;
isMP = contains2(configID,'MP');
isPBP = contains2(configID,'PBP');
isBP = contains2(configID,'BP') && ~isPBP;
isPTP = contains2(configID,'PTP');
isTP = contains2(configID,'TP') && ~isPTP;
isPartial = isPBP || isPTP;
isCG = contains2(configID,'CG');
isQuit = false;
buttonHandle = gobjects(1);

% Display
% if isBP || isTP
    rowNames = {...
        'Active Epol (V)','Return Epol (V)','Vd (V)', ...
        'Val (V)','Vat (V)', ...
        'Ral (kOhm)','Rat (kOhm)'};
% else
%     rowNames = {...
%         'Epol (V)','Vd (V)', ...
%         'Val (V)','Vat (V)', ...
%         'Ral (kOhm)','Rat (kOhm)'};
% end

% Channels
channel_arr = [File.Data(:).ActiveChannel];
groupNum = length(channel_arr);
channelNum = channel_arr(groupNum);
channelName = File.Data(groupNum).Name;
channelGroup_mat = File.Test.Groups;
[numOfGroups,~] = size(channelGroup_mat);

% Captures
capture_arr = [File.Data(groupNum).Capture(:).Index];
numOfCaptures = length(capture_arr);
captureNum = capture_arr(numOfCaptures);
Capture = File.Data(groupNum).Capture(captureNum);

% Experiment
expType = File.Test.Experiment;
isPulsing = contains2(expType,{'SP','LP'});
isTriphasic = contains2(expType,'TV');

% Test
stimType = File.Test.ID;
isTargetMax = contains2(stimType,'max');
returnElectrode = File.Parameters.CounterElectrode.Type;
returnOCP = File.Parameters.CounterElectrode.OpenCircuitPotential;

% Environment
environment = File.Parameters.Environment;
isAnimal = contains2(environment,'Animal');

%% Fields
field_cell = fieldnames(Capture);
timeField_idx = find(containsi(field_cell,'Time'));
currentDensity_idx = find(containsi(field_cell,'CurrentDensity'));
dataFields_cell = field_cell(timeField_idx+1:currentDensity_idx-1);
isCurrentField_tf = containsi(dataFields_cell,'curr');
hasCurrent = any(isCurrentField_tf);
voltFields_cell = dataFields_cell(~isCurrentField_tf);
activeField_tf = containsi(dataFields_cell,{'act','work','pot'});
hasActiveField = any(activeField_tf);
diffField_tf = containsi(dataFields_cell,{'diff'});
hasDiffField = any(diffField_tf);
voltageField_tf = containsi(dataFields_cell,{'volt'});
voltageField_name = dataFields_cell{voltageField_tf};
hasVoltageField = any(voltageField_tf);
if hasActiveField
    specialField_idx = find(activeField_tf);
    activeField_name = dataFields_cell{activeField_tf};
elseif hasDiffField
    specialField_idx = find(diffField_tf);
    activeField_name = dataFields_cell{diffField_tf};
elseif hasVoltageField
    specialField_idx = find(voltageField_tf);
    activeField_name = dataFields_cell{voltageField_tf};
else
    specialField_idx = 1;
end
specialField = dataFields_cell{specialField_idx};

%% Oscilloscope
numOfScopeChannels = sum(File.Oscilloscope(:).NumberOfChannels);
channelSelectList = vertcat(File.Oscilloscope(:).Channels);
fieldsList = vertcat(File.Oscilloscope(:).Fields);
activeChannel_tf = containsi(fieldsList,{'act','work','pot'});
diffChannel_tf = containsi(fieldsList,{'diff'});
voltageChannel_tf = containsi(fieldsList,{'volt'});
if any(activeChannel_tf)
    specialChannel_idx = find(activeChannel_tf);
elseif any(diffChannel_tf)
    specialChannel_idx = find(diffChannel_tf);
elseif any(voltageChannel_tf)
    specialChannel_idx = find(voltageChannel_tf);
else
    specialChannel_idx = 1;
end
specialChannel = fieldsList(specialChannel_idx);
isVoltageSpecial = contains2(specialChannel,'volt');
hasReturnChannel = contains2(fieldsList,{'ret','count'});
if hasReturnChannel
    returnField_tf = containsi(dataFields_cell,{'ret','count'});
    returnField_name = dataFields_cell{returnField_tf};
end
hasAltActive = isVoltageSpecial && hasReturnChannel;
% if hasAltActive
isAlwaysGetVoltage = true;
% else
%     isAlwaysGetVoltage = false;
% end
currentChannel_tf = containsi(fieldsList,'curr');
currentChannel_idx = find(currentChannel_tf);
currentScopeChannel = channelSelectList{currentChannel_idx};
isWaveformNorm_tf = zeros(numOfScopeChannels,1);

%% Pattern
% amplitude
amplitude1 = File.Parameters.Amplitude1(groupNum);
amplitude1_mag = abs(amplitude1);
amplitude2 = File.Parameters.Amplitude2(groupNum);
amplitude2_mag = abs(amplitude2);
if isTriphasic
    amplitude3 = File.Parameters.Amplitude3(groupNum);
    amplitude3_mag = abs(amplitude3);
else
    amplitude3_mag = NaN;
end
% phase width
phaseWidth1 = File.Parameters.PhaseWidth1;        % first phase pulse width
phaseWidth2 = File.Parameters.PhaseWidth2;        % second phase pulse width
if isTriphasic
    phaseWidth3 = File.Parameters.PhaseWidth3;        % second phase pulse width
end
% between time
interphaseDelay = File.Parameters.InterphaseDelay;% interphase delay
hasInterphaseDelay = interphaseDelay > 0;
dischargeDelay = File.Parameters.DischargeDelay;
hasDischargeDelay = dischargeDelay > 0;
phaseNames = {'Phase 1','Phase 2'};
if isTriphasic
    phaseNames = [phaseNames 'Phase 3'];
end
depolTime = File.Parameters.Depolarization;
polarity = File.Parameters.Polarity;
chargeInjection = File.Data(groupNum).Capture(captureNum).ChargeInjection;

%% Electrode
surfaceArea = File.Data(groupNum).SurfaceArea;
if hasAltActive || hasActiveField || hasDiffField
    electrode = 'ReferenceElectrode';
else
    electrode = 'CounterElectrode';
end
switch polarity
    case -1
        potentialLimit = File.Parameters.(electrode).LowerPotential;
    case 1
        potentialLimit = File.Parameters.(electrode).UpperPotential;
end
potentialLimit = abs(potentialLimit);
pulseWidth_arr = File.Parameters.PulseWidth;
numOfPulseWidth = length(pulseWidth_arr);

% Amplitude
if captureNum > 1
    amplitude_arr = [File.Data(groupNum).Capture(:).Amplitude];
    isAmplitudeRepeat_tf = ismember(amplitude_arr,amplitude1);
    isAmplitudeRepeat_idx = find(isAmplitudeRepeat_tf);
    isAmplitudeRepeat = length(isAmplitudeRepeat_idx) > 1;
    % isAmplitudeRepeat = any(ismember(amplitude_arr,amplitude1));
else
    isAmplitudeRepeat = false;
end
targetCharge = File.Test.ChargePhase;
targetAmplitude = roundStim(targetCharge / phaseWidth1 * 1e3);
isAtFixedTarget = isequal(amplitude1_mag,targetAmplitude);

% Initialization
% zeroCurrent_idx = 4;
% Waveform
chargingCapacitance = NaN;
status = 'Good';
isTooLong = File.Data(groupNum).Capture(captureNum).Status.TooLong;
try
    isPrecise = File.Data(groupNum).Capture(captureNum).Status.Precise;
catch
    File.Data(groupNum).Capture(captureNum).Status.Precise = true;
    isPrecise = File.Data(groupNum).Capture(captureNum).Status.Precise;
end
isBad = false;
try
    isLimitReached = File.Data(groupNum).Capture(captureNum-1). ...
        Status.PotentialLimit;
    isVoltageSafe = File.Data(groupNum).Capture(captureNum-1). ...
        Status.VoltageSafety;
    isAtVoltageCompliance = File.Data(groupNum).Capture(captureNum-1). ...
        Status.VoltageCompliance;
catch
    isVoltageSafe = true;
    isLimitReached = 0;
    isAtVoltageCompliance = false;
end
isAlwaysGetCurrent = isPulsing;
attemptNum = 1;
displayTable = [];
currentDensity = [];
excursion_arr = [];
isAltActiveFound = false;
isVoltageFound = false;
isReturnFound = false;
isCancel = false;
isCapacitive = false;

% Polarization
polMethod = File.Parameters.PolarizationMethod;
isPolAtTime = contains2(polMethod,'time');
if isPolAtTime
    % Phase 1
    excursion1_time = phaseWidth1 + depolTime;
    % Phase 2
    afterPhase2 = phaseWidth1 + interphaseDelay + phaseWidth2;
    excursion2_time = afterPhase2 + depolTime;
    if isTriphasic
        afterPhase3 = afterPhase2 + interphaseDelay + phaseWidth3;
        excursion3_time = afterPhase3 + depolTime;
    end
end

% Charge
amplitude_mag_arr = [amplitude1_mag amplitude2_mag amplitude3_mag];
isAtMaxCurrent = any(amplitude_mag_arr >= 1e3);

% Thresholds
% badThresh = 1;
if isAnimal
    if captureNum > 1
        badThresh = potentialLimit + 1;
    else
        badThresh = potentialLimit + 0.5;
    end
else
    if captureNum > 5
        badThresh = potentialLimit + 0.3;
    elseif captureNum > 1
        badThresh = potentialLimit + 0.25;
    else
        badThresh = potentialLimit + 0.1;
    end
end
if phaseWidth1 >= 100
    maxAttempts = 4;
else
    maxAttempts = 5;
end

%% Time
digitalDelay = File.Stimulator.DigitalDelay;
[File,time_raw] = getTime(File);
time = time_raw * 1e6 - digitalDelay;
File.Data(groupNum).Capture(captureNum).Time = time;
fprintf('\tProcessing time...');
startTime = tic;
prepulse_tf = time < 0;
if interphaseDelay > 0
    % index max potential
    if numOfPulseWidth > 1
        excursion1_idx = find(time <= excursion1_time,1,'last');
        excursion2_idx = find(time <= excursion2_time,1,'last');
        if isTriphasic
            excursion3_idx = find(time <= excursion3_time,1,'last');
        end
    else
        excursion1_idx = find(time >= excursion1_time,1,'first');
        excursion2_idx = find(time >= excursion2_time,1,'first');
        if isTriphasic
            excursion3_idx = find(time >= excursion3_time,1,'first');
        end
    end
end
phaseWith1_half_idx = find(time <= phaseWidth1 / 2,1);
phaseWith2_half_idx = find(time <= phaseWidth1 + interphaseDelay + phaseWidth2 / 2,1);
if isTriphasic
    phaseWith3_half_idx = find(time <= phaseWidth1 + 2* interphaseDelay + phaseWidth2 + phaseWidth3 / 2,1);
end
[endTime,unit] = getEndTime(startTime);
fprintf('OK \t\t(%.2f %s)\n',endTime,unit);

%% Waveform
while ~all(isWaveformNorm_tf)
    scopeChannel_idx = 0;
    for deviceNum = 1:numOfDevices
        scopeChannelSelect_cell = File.Oscilloscope(deviceNum).Channels;
        fields_cell = File.Oscilloscope(deviceNum).Fields;
        numOfScopeChannels = File.Oscilloscope(deviceNum).NumberOfChannels;
        for channel_idx = 1:numOfScopeChannels
            scopeChannel_idx = scopeChannel_idx + 1;
            isWaveformNorm = isWaveformNorm_tf(scopeChannel_idx);
            if ~isWaveformNorm
                scopeChannel = scopeChannelSelect_cell{channel_idx};
                field = fields_cell{channel_idx};
                isVoltChannel = contains2(voltFields_cell,field);
                isSpecialChannel = contains2(field,specialChannel);
                isVoltageChannel = contains2(field,'volt');
                isCurrentChannel = contains2(field,'curr');
                isReturnChannel = contains2(field,{'ret','count'});
                if isReturnChannel
                    hasReturnData = true;
                else
                    hasReturnData = false;
                end

                data = File.Data(groupNum).Capture(captureNum).(field);

                % if isVoltageChannel
                %     voltage_arr = data;
                % elseif isReturnChannel
                %     return_arr = data;
                % end

                %% Voltage
                isAtLimits = isLimitReached || isBad;
                isAtTarget = ~isTargetMax || isAtMaxCurrent;
                isManyAttempts = attemptNum > maxAttempts;
                isTimingBad = isTooLong || isManyAttempts || (isAmplitudeRepeat || ~isPrecise);
                isWaveformNeeded = isempty(data) || isAtLimits || isAtTarget || isTimingBad;
                isVoltNeeded = isVoltChannel && ...
                    (isSpecialChannel || isWaveformNeeded || isAlwaysGetVoltage);
                if isVoltNeeded
                    data = getWaveform3(File,deviceNum,scopeChannel);
                    File.Data(groupNum).Capture(captureNum).(field) = data;
                    varargout{1} = File;
                    
                    % Capacitance
                    if ~isReturnChannel
                        [capacitance_check,~,~]  = getCapacitance(File,time,data);
                    else
                        fprintf('\t');
                    end
                    isCapacitive = any(capacitance_check);
                    if isReturnChannel
                        % return_arr = File.Data(groupNum).Capture(captureNum).(returnField_name);
                        % if isMP
                        % data = smooth(smooth(smooth(data)));
                        % end
                        returnOCP = mean(data(prepulse_tf));
                        File.Parameters.CounterElectrode.OpenCircuitPotential = returnOCP;
                        [File,isQuit] = setArduinoVoltage(File);
                        isReturnFound = true;
                    end
                    if isVoltageChannel
                        isVoltageFound = true;
                        voltage_arr = File.Data(groupNum).Capture(captureNum).(voltageField_name);
                    end
                    if hasAltActive && (isVoltageFound && isReturnFound)
                        fprintf('Calculating active waveform...');
                        startTime = tic;
                        % if isBP
                        %     active_arr = voltage_arr + returnOCP;
                        % else
                        return_arr = File.Data(groupNum).Capture(captureNum).(returnField_name);
                        data = voltage_arr + return_arr;
                        potential_arr = data;
                        % end
                        File.Data(groupNum).Capture(captureNum).(activeField_name) = potential_arr;
                        isAltActiveFound = true;
                        [endTime,unit] = getEndTime(startTime);
                        fprintf('OK \t\t(%.2f %s)\n',endTime,unit);
                    else
                        isAltActiveFound = false;
                    end
                    isActiveFound = contains2(field,'active');
                    % figure(groupNum);
                    % clf;
                    % plot(time,data); hold on;
                    % xlabel('Time (\mus)');
                    % ylabel([field ' ' '(' channelUnit ')']);
                    if (isSpecialChannel && ~hasAltActive) ...
                            || isAltActiveFound || isActiveFound
                        updateWaitbar(File,'Getting metrics...');
                        fprintf('Getting metrics...\n');
                        if isActiveFound
                            potential_arr = File.Data(groupNum).Capture(captureNum).(specialField);
                            voltage_arr = File.Data(groupNum).Capture(captureNum).Voltage;
                        else
                            voltage_arr = File.Data(groupNum).Capture(captureNum).Voltage;
                            if isempty(voltage_arr)
                                % display(voltage_arr);
                                error('voltage_arr');
                            end
                            if isPulsing
                                potential_arr = data;
                            else
                                if hasAltActive
                                    potential_arr = voltage_arr + return_arr;
                                else
                                    potential_arr = voltage_arr;
                                end
                            end
                            if isempty(potential_arr)
                                % display(potential_arr)
                                error('potential_arr');
                            end
                        end

%                         if ~isCapacitive
                            varargout{1} = File;
                            % try
                            [ ...
                                drivingVoltage_arr,driving_idx_arr, ...
                                accessVoltage_arr, ...
                                accessResistance_arr, ...
                                access_idx_arr, ...
                                isCancel] = getVoltageMetrics( ...
                                File,time,potential_arr,voltage_arr);
                            % catch
                            %     display(drivingVoltage_arr);
                            %     display(driving_idx_arr);
                            %     display(accessVoltage_arr);
                            %     display(accessResistance_arr);
                            %     display(access_idx_arr);
                            %     error('One or more output arguments not assigned during call to "varargout".');
                            % end
%                         end

                        % Access
                        phase1_sign_check = sign(potential_arr(phaseWith1_half_idx));
                        phase2_sign_check = sign(potential_arr(phaseWith2_half_idx));
                        if isTriphasic
                            phase3_sign_check = sign(potential_arr(phaseWith3_half_idx));
                        else
                            phase3_sign_check = 0;
                        end
                        isAccessEmpty = isempty(accessVoltage_arr) || ...
                            isempty(accessResistance_arr) || ...
                            isempty(access_idx_arr);
                        isGetAccess = (isCancel && ...
                            phase1_sign_check + phase2_sign_check + phase3_sign_check == 0) ... % isCapacitive || 
                            || isAccessEmpty;
                        if isGetAccess
                            [accessVoltage_arr,accessResistance_arr,access_idx_arr] = ...
                                getAccess2(File,time,potential_arr);
                            % else
                            %     if hasInterphaseDelay
                            %         numOfAccess = 4;
                            %     else
                            %         numOfAccess = 2;
                            %     end
                            %     accessVoltage_arr = NaN(1,numOfAccess);
                            %     accessResistance_arr = NaN(1,numOfAccess);
                            %     access_idx = NaN(1,numOfAccess);
                        end
                        % catch err
                        %     display(err);
                        % end

                        % Access
                        if ~(isCapacitive || isCancel)
                            try
                                accessVoltage1 = accessVoltage_arr(1);
                                accessResistance1 = accessResistance_arr(1);
                                access1_idx = access_idx_arr(1); %#ok<*UNRCH>
                                accessVoltage1_time = time(access1_idx);
                                accessVoltage1_plot = potential_arr(access1_idx);
                            catch
                                accessVoltage1 = NaN;
                                accessResistance1 = NaN;
                                accessVoltage1_time = NaN;
                                accessVoltage1_plot = NaN;
                            end

                            if hasInterphaseDelay
                                try
                                    % Voltage
                                    accessVoltage2 = accessVoltage_arr(2);
                                    accessVoltage3 = accessVoltage_arr(3);

                                    % Resistance
                                    accessResistance2 = accessResistance_arr(2);
                                    accessResistance3 = accessResistance_arr(3);

                                    % Plot
                                    access2_idx = access_idx_arr(2);
                                    access3_idx = access_idx_arr(3);
                                    accessVoltage2_time = time(access2_idx);
                                    accessVoltage3_time = time(access3_idx);
                                    accessVoltage2_plot = potential_arr(access2_idx);
                                    accessVoltage3_plot = potential_arr(access3_idx);

                                catch
                                    % Voltage
                                    accessVoltage2 = NaN;
                                    accessVoltage3 = NaN;

                                    % Resistance
                                    accessResistance2 = NaN;
                                    accessResistance3 = NaN;

                                    % Plot
                                    accessVoltage2_time = NaN;
                                    accessVoltage3_time = NaN;
                                    accessVoltage2_plot = NaN;
                                    accessVoltage3_plot = NaN;
                                end
                            else
                                % Voltage
                                accessVoltage2 = NaN;
                                accessVoltage3 = NaN;

                                % Resistance
                                accessResistance2 = NaN;
                                accessResistance3 = NaN;

                                % Plot
                                accessVoltage2_time = NaN;
                                accessVoltage3_time = NaN;
                                accessVoltage2_plot = NaN;
                                accessVoltage3_plot = NaN;
                            end

                            if hasDischargeDelay
                                try
                                    accessVoltage4 = accessVoltage_arr(4);
                                    accessResistance4 = accessResistance_arr(4);
                                    access4_idx = access_idx_arr(4);
                                    accessVoltage4_time = time(access4_idx);
                                    accessVoltage4_plot = potential_arr(access4_idx);
                                catch
                                    accessVoltage4 = NaN;
                                    accessResistance4 = NaN;
                                    accessVoltage4_time = NaN;
                                    accessVoltage4_plot = NaN;
                                end
                            else
                                accessVoltage4 = NaN;
                                accessResistance4 = NaN;
                                accessVoltage4_time = NaN;
                                accessVoltage4_plot = NaN;
                            end

                        else
                            try
                                accessVoltage1 = accessVoltage_arr(1);
                                accessResistance1 = accessResistance_arr(1);
                                access1_idx = access_idx_arr(1);
                                accessVoltage1_time = time(access1_idx);
                                accessVoltage1_plot = potential_arr(access1_idx);
                            catch
                                accessVoltage1 = NaN;
                                accessResistance1 = NaN;
                                accessVoltage1_time = NaN;
                                accessVoltage1_plot = NaN;
                            end

                            if hasInterphaseDelay
                                try
                                    % Voltage
                                    accessVoltage3 = accessVoltage_arr(2);

                                    % Resistance
                                    accessResistance3 = accessResistance_arr(2);

                                    % Plot
                                    access2_idx = access_idx_arr(2);
                                    accessVoltage3_time = time(access2_idx);
                                    accessVoltage3_plot = potential_arr(access2_idx);
                                catch
                                    accessVoltage3 = NaN;
                                    accessResistance3 = NaN;
                                    accessVoltage3_time = NaN;
                                    accessVoltage3_plot = NaN;
                                end
                            else
                                accessVoltage3 = NaN;
                                accessResistance3 = NaN;
                                accessVoltage3_time = NaN;
                                accessVoltage3_plot = NaN;
                            end
                        end

                        if isTriphasic
                            if hasInterphaseDelay
                                try
                                    accessVoltage5 = accessVoltage_arr(5);
                                    accessResistance5 = accessResistance_arr(5);
                                    access5_idx = access_idx_arr(5);
                                    accessVoltage5_time = time(access5_idx);
                                    accessVoltage5_plot = potential_arr(access5_idx);
                                catch
                                    accessVoltage5 = NaN;
                                    accessResistance5 = NaN;
                                    accessVoltage5_time = NaN;
                                    accessVoltage5_plot = NaN;
                                end
                            else
                                accessVoltage5 = NaN;
                                accessResistance5 = NaN;
                                accessVoltage5_time = NaN;
                                accessVoltage5_plot = NaN;
                            end

                            if hasDischargeDelay
                                try
                                    accessVoltage6 = accessVoltage_arr(6);
                                    accessResistance6 = accessResistance_arr(6);
                                    access6_idx = access_idx_arr(6);
                                    accessVoltage6_time = time(access6_idx);
                                    accessVoltage6_plot = potential_arr(access6_idx);
                                catch
                                    accessVoltage6 = NaN;
                                    accessResistance6 = NaN;
                                    accessVoltage6_time = NaN;
                                    accessVoltage6_plot = NaN;
                                end
                            else
                                accessVoltage6 = NaN;
                                accessResistance6 = NaN;
                                accessVoltage6_time = NaN;
                                accessVoltage6_plot = NaN;
                            end
                            
                        else
                            accessVoltage5 = [];
                            accessResistance5 = [];
                            accessVoltage5_time = [];
                            accessVoltage5_plot = [];

                            accessVoltage6 = [];
                            accessResistance6 = [];
                            accessVoltage6_time = [];
                            accessVoltage6_plot = [];
                        end

                        % Driving
                        isDrivingMissing = isempty(drivingVoltage_arr) || isempty(driving_idx_arr);
                        if isCapacitive || isCancel || isDrivingMissing
                            fprintf('\t');
                            [drivingVoltage_arr,driving_idx_arr] = getDriving2( ...
                                File,time,voltage_arr,'both');
                        end
                        driving1_idx = driving_idx_arr(1);
                        driving2_idx = driving_idx_arr(2);
                        % Voltage
                        drivingVoltage1 = drivingVoltage_arr(1);
                        drivingVoltage2 = drivingVoltage_arr(2);
                        % drivingVoltage1 = abs(voltage_arr(driving1_idx));
                        % Plot
                        drivingVoltage1_time = time(driving1_idx);
                        drivingVoltage2_time = time(driving2_idx);
                        drivingVoltage1_plot = voltage_arr(driving1_idx);
                        drivingVoltage2_plot = voltage_arr(driving2_idx);

                        if isTriphasic
                            try
                                driving3_idx = driving_idx_arr(3);
                                drivingVoltage3 = drivingVoltage_arr(3);
                                drivingVoltage3_time = time(driving3_idx);
                                drivingVoltage3_plot = voltage_arr(driving3_idx);
                            catch
                                drivingVoltage3 = NaN;
                                drivingVoltage3_time = NaN;
                                drivingVoltage3_plot = NaN;
                            end
                        else
                            drivingVoltage3 = [];
                            drivingVoltage3_time = [];
                            drivingVoltage3_plot = [];
                        end

                        if isCapacitive || isCancel
                            % End of phases
                            fprintf('\t');
                            endPhase_idx = getEndPhase(File,time,potential_arr,'both');
                            % Voltage
                            if hasInterphaseDelay
                                endPhase1_idx = endPhase_idx(1);
                                excursion1_zero = potential_arr(endPhase1_idx);
                                accessVoltage2 = drivingVoltage1_plot - excursion1_zero;
                            else
                                accessVoltage2 = NaN;
                            end
                            if hasDischargeDelay
                                endPhase2_idx = endPhase_idx(2);
                                excursion2_zero = potential_arr(endPhase2_idx);
                                accessVoltage4 = drivingVoltage2_plot - excursion2_zero;
                            else
                                endPhase2_idx = [];
                                accessVoltage4 = NaN;
                            end

                            % Resistance
                            amplitude1_A = amplitude1_mag * 1e-6;
                            amplitude2_A = amplitude2_mag * 1e-6;
                            accessResistance2 = abs(accessVoltage2) / amplitude1_A * 1e-3;
                            accessResistance4 = abs(accessVoltage4) / amplitude2_A * 1e-3;

                            % Plot
                            accessVoltage2_plot = potential_arr(endPhase1_idx);
                            accessVoltage4_plot = potential_arr(endPhase2_idx);
                            accessVoltage2_time = time(endPhase1_idx);
                            accessVoltage4_time = time(endPhase2_idx);
                        end

                        % Potential Excursion
                        fprintf('\t');
                        fprintf('Getting potential excursion...');
                        startTime = tic;

                        % Phase 1
                        fprintf('Phase 1...');
                        if isPolAtTime
                            excursion1 = potential_arr(excursion1_idx);
                        else
                            if hasInterphaseDelay
                                access_test_idx = access2_idx;
                                if ~isVoltageChannel
                                    excursion1 = potential_arr(access_test_idx);
                                else
                                    excursion1 = voltage_arr(access_test_idx);
                                end
                                excursion1_time = time(access_test_idx);
                            else
                                access_test_idx = access1_idx;
                                if ~isVoltageChannel
                                    excursion1 = activeDriving_arr(1) - potential_arr(access_test_idx);
                                else
                                    excursion1 = drivingVoltage_arr(1) * polarity - voltage_arr(access_test_idx);
                                end
                                excursion1_time = NaN;
                            end
                            excursion1_idx = access_test_idx;
                            
                        end
                        if hasReturnData
                            returnExcursion1 = return_arr(excursion1_idx);
                        end

                        % Phase 2
                        fprintf('Phase 2...');
                        if isPolAtTime
                            excursion2 = potential_arr(excursion2_idx);
                        else
                            if (~isTriphasic && hasDischargeDelay) || (isTriphasic && hasInterphaseDelay)
                                access_test_idx = access4_idx;
                                if ~isVoltageChannel
                                    excursion2 = potential_arr(access_test_idx);
                                else
                                    excursion2 = voltage_arr(access_test_idx);
                                end
                                excursion2_time = time(access_test_idx);
                            else
                                access_test_idx = access3_idx;
                                if ~isVoltageChannel
                                    excursion2 = activeDriving_arr(2) - potential_arr(access_test_idx);
                                else
                                    excursion2 = drivingVoltage_arr(2) * polarity - voltage_arr(access_test_idx);
                                end
                                excursion2_time = NaN;
                            end
                            excursion2_idx = access_test_idx;
                        end
                        if hasReturnData
                            returnExcursion2 = return_arr(excursion2_idx);
                        end

                        % Phase 3
                        if isTriphasic
                            fprintf('Phase 3...');
                            if isPolAtTime
                                excursion3 = potential_arr(excursion3_idx);
                            else
                                if hasDischargeDelay
                                    access_test_idx = access6_idx;
                                    if ~isVoltageChannel
                                        excursion3 = potential_arr(access_test_idx);
                                    else
                                        excursion3 = voltage_arr(access_test_idx);
                                    end
                                    excursion3_time = time(access_test_idx);
                                else
                                    access_test_idx = access5_idx;
                                    if ~isVoltageChannel
                                        excursion3 = activeDriving_arr(3) - potential_arr(access_test_idx);
                                    else
                                        excursion3 = drivingVoltage_arr(3) * polarity - voltage_arr(access_test_idx);
                                    end
                                    excursion3_time = NaN;
                                end
                                excursion3_idx = access_test_idx;
                            end
                            if hasReturnData
                                returnExcursion3 = return_arr(excursion3_idx);
                            end
                        else
                            excursion3 = NaN;
                            excursion3_time = NaN;
                            if hasReturnData
                                returnExcursion3 = NaN;
                            end
                        end

                        %% Store
                        % potential excursion
                        excursion_arr = [excursion1 excursion2 excursion3];
                        File.Data(groupNum).Capture(captureNum). ...
                            PotentialExcursion = excursion_arr;
                        % if isBP || isTP
                            returnExcursion_arr = [returnExcursion1 returnExcursion2 returnExcursion3];
                            File.Data(groupNum).Capture(captureNum).ReturnExcursion = returnExcursion_arr;
                        % end
                        % driving voltage
                        File.Data(groupNum).Capture(captureNum). ...
                            DrivingVoltage = drivingVoltage_arr;
                        effectiveCapacitance = chargeInjection / max(drivingVoltage_arr);
                        File.Data(groupNum).Capture(captureNum). ...
                            EffectiveCapacitance = effectiveCapacitance;
                        % access voltage
                        accessVoltage_mat = [ ...
                            accessVoltage1,accessVoltage2, ...
                            accessVoltage3,accessVoltage4, ...
                            accessVoltage5,accessVoltage6];
                        File.Data(groupNum).Capture(captureNum). ...
                            AccessVoltage = accessVoltage_mat;
                        % access resistance
                        accessResistance_mat = [ ...
                            accessResistance1,accessResistance2, ...
                            accessResistance3,accessResistance4, ...
                            accessResistance5,accessResistance6];
                        File.Data(groupNum).Capture(captureNum). ...
                            AccessResistance = accessResistance_mat;

                        [endTime,unit] = getEndTime(startTime);
                        fprintf('OK \t\t(%.2f %s)\n',endTime,unit);

                        % Check if broken
                        active_arr_mag = abs(potential_arr);
                        isAtVoltageCompliance = any(active_arr_mag > COMPLIANCE_THRESH);
                        if isAtVoltageCompliance
                            status = 'Voltage compliance reached';
                            for scopeChannel_idx_alt = 1:numOfScopeChannels
                                if ~isequal(scopeChannel_idx_alt,scopeChannel_idx)
                                    isWaveformNorm_tf(scopeChannel_idx_alt) = true;
                                end
                            end
                        else
                            % fprintf('\t');
                            [isLimitReached,status] = checkPotentialExcursion(File,electrode);
                            limitStatus = status;
                            File.Data(groupNum).Capture(captureNum).Status. ...
                                PotentialLimit = isLimitReached;
                            isVoltageSafe = ~(abs(excursion1) > badThresh);
                            if ~isVoltageSafe
                                status = 'Voltage is unsafe';
                                for scopeChannel_idx_alt = 1:numOfScopeChannels
                                    if ~isequal(scopeChannel_idx_alt,scopeChannel_idx)
                                        isWaveformNorm_tf(scopeChannel_idx_alt) = true;
                                    end
                                end
                            end
                        end

                        % Capacitance
%                         fprintf('\t');
                        [chargingCapacitance,capacitance_time,capacitance_line] ...
                            = getCapacitance(File,time,data);

                        % Check if broken
                        isBad = isAtVoltageCompliance || ~isVoltageSafe;
                        isGood = ~isBad;
                        File.Data(groupNum).Capture(captureNum). ...
                            ChargingCapacitance = chargingCapacitance;
                        File.Data(groupNum).Capture(captureNum).Status. ...
                            Good = isGood;
                        File.Data(groupNum).Capture(captureNum).Status. ...
                            PotentialLimit = isLimitReached;
                        File.Data(groupNum).Capture(captureNum).Status. ...
                            VoltageSafety = isVoltageSafe;
                        File.Data(groupNum).Capture(captureNum).Status. ...
                            VoltageCompliance = isAtVoltageCompliance;

                        % Values
                        % time_arr
                        voltageValue_arr_time = [...
                            drivingVoltage1_time,drivingVoltage2_time,drivingVoltage3_time, ...
                            accessVoltage1_time,accessVoltage2_time, ...
                            accessVoltage3_time,accessVoltage4_time, ...
                            accessVoltage5_time,accessVoltage6_time];
                        File.Data(groupNum).Capture(captureNum).Measurement. ...
                            VoltageValues.Time = voltageValue_arr_time;
                        % voltage
                        voltageValue_arr_volt = [...
                            drivingVoltage1,drivingVoltage2,drivingVoltage3, ...
                            accessVoltage1,accessVoltage2, ...
                            accessVoltage3,accessVoltage4, ...
                            accessVoltage5,accessVoltage6];
                        File.Data(groupNum).Capture(captureNum).Measurement. ...
                            VoltageValues.Voltage = voltageValue_arr_volt;
                        % plot
                        voltagePlot_arr = [...
                            drivingVoltage1_plot,drivingVoltage2_plot,drivingVoltage3_plot, ...
                            accessVoltage1_plot,accessVoltage2_plot, ...
                            accessVoltage3_plot,accessVoltage4_plot, ...
                            accessVoltage5_plot,accessVoltage6_plot];
                        File.Data(groupNum).Capture(captureNum).Measurement. ...
                            VoltageValues.Plot = voltagePlot_arr;
                        % Potential Excursion
                        % time_arr
                        excursion_arr_time = [excursion1_time excursion2_time excursion3_time];
                        File.Data(groupNum).Capture(captureNum).Measurement.PotentialExcursion.Time = excursion_arr_time;
                        % voltage
                        excursion_arr_volt = [excursion1 excursion2 excursion3];
                        File.Data(groupNum).Capture(captureNum).Measurement. ...
                            PotentialExcursion.Voltage = excursion_arr_volt;
                        % if isBP || isTP
                            File.Data(groupNum).Capture(captureNum).Measurement. ...
                                ReturnExcursion.Voltage = returnExcursion_arr;
                        % end

                        % Display
                        if ~isAtVoltageCompliance
                            phase1 = [...
                                excursion1;returnExcursion1;drivingVoltage1; ...
                                accessVoltage1;accessVoltage2; ...
                                accessResistance1;accessResistance2];
                            phase1_round = round(phase1,3,'significant');
                            if isTriphasic
                                if hasInterphaseDelay
                                    phase2 = [...
                                        excursion2;returnExcursion2;drivingVoltage2; ...
                                        accessVoltage3;accessVoltage4; ...
                                        accessResistance3;accessResistance4];
                                end
                                if hasDischargeDelay
                                    phase3 = [...
                                        excursion3;returnExcursion3;drivingVoltage3; ...
                                        accessVoltage5;accessVoltage6; ...
                                        accessResistance5;accessResistance6];
                                else
                                    phase3 = [...
                                        excursion3;returnExcursion3;drivingVoltage3; ...
                                        accessVoltage5;NaN; ...
                                        accessResistance5;NaN];
                                end
                            else
                                if hasDischargeDelay
                                    phase2 = [...
                                        excursion2;returnExcursion2;drivingVoltage2; ...
                                        accessVoltage3;accessVoltage4; ...
                                        accessResistance3;accessResistance4];
                                else
                                    phase2 = [...
                                        excursion2;returnExcursion2;drivingVoltage2; ...
                                        accessVoltage3;NaN; ...
                                        accessResistance3;NaN];
                                end
                            end
                            phase2_round = round(phase2,3,'significant');
                            if isTriphasic
                                phase3_round = round(phase3,3,'significant');
                                displayTable = table(phase1_round,phase2_round,phase3_round, ...
                                'VariableNames',phaseNames, ...
                                'RowNames',rowNames);
                            else
                                displayTable = table(phase1_round,phase2_round, ...
                                'VariableNames',phaseNames, ...
                                'RowNames',rowNames);
                            end
                            
                            disp(displayTable);
                        end
                        if any(chargingCapacitance)
                            capacitance_use = addCommas(chargingCapacitance);
                            fprintf('\tC = %s nF\n',capacitance_use);
                        end
                    end
                end

                %% Current
                isAtLimits = any(isLimitReached) || isBad;
                isAtTarget = isAtFixedTarget || isAtMaxCurrent;
                isManyAttempts = attemptNum > maxAttempts;
                isTimingBad = isTooLong || isManyAttempts || ~isPrecise;
                isPastCurrent = scopeChannel_idx > currentChannel_idx;
                isWaveformNeeded = isAtLimits || isAtTarget || isTimingBad;
                hasCurrent = ~isempty(currentDensity);
                isCurrentNeeded = ~hasCurrent && ...
                    (isWaveformNeeded ...
                    || (isCurrentChannel && isAlwaysGetCurrent) ...
                    || (isPastCurrent && isWaveformNeeded));
                if isCurrentNeeded
                    current_arr = getWaveform2(File,deviceNum,currentScopeChannel);
                    File.Data(groupNum).Capture(captureNum).Current = current_arr;
                    currentDensity = getCurrentDensity(current_arr,surfaceArea, ...
                        'uA','A');
                    File.Data(groupNum).Capture(captureNum). ...
                        CurrentDensity = currentDensity;
                    hasCurrent = true;
                    % end
                end
            end

            %% Plot
            fig = figure(groupNum);
            if isPulsing
                pulseNum = File.Data(groupNum).Capture(captureNum).PulseNumber;
                pulseNum_use = addCommas(pulseNum);
                figName = sprintf('%s (%s pulses)', ...
                    channelName,pulseNum_use);
            else
                if numOfGroups > 16
                    figName = sprintf('%s (Capture %d) (%d / %d)', ...
                        channelName,captureNum,groupNum,numOfGroups);
                else
                    figName = sprintf('%s (Capture %d)', ...
                        channelName,captureNum);
                end
            end

            set(fig,'Name',figName);
            % fig_trend = figure(groupNum+numOfGroups);
            % figName_trend = sprintf('%s Trend',channelName);
            % set(fig_trend,'Name',figName_trend);

            [buttonHandle,ax] = getVoltageTransientPlot(File);
            hold(ax,'on');

            % Plot title
            % Check
            % [isAnteBad,isPosteBad] = checkWaveform(File,data);
            % isDataBad = isAnteBad || isPosteBad || isempty(data);
            isDataBad = false;
            isWaveformNorm = ~isDataBad;
            isWaveformNorm_tf(scopeChannel_idx) = isWaveformNorm;
            if any(chargingCapacitance)
                if ~isempty(currentDensity)
                    yyaxis left;
                end
                plot(ax,capacitance_time,capacitance_line,'k:', ...
                    'LineWidth',2, ...
                    'HandleVisibility','off');
            end
            if any(isLimitReached)
                % switch isLimitReached
                %     case -1
                %         limitStatus = 'Cathodic Potential Limit Reached';
                %     case 1
                %         limitStatus = 'Anodic Potential Limit Reached';
                %     case -2
                %         limitStatus = 'Return Cathodic Potential Limit Reached';
                %     case 2
                %         limitStatus = 'Return Anodic Potential Limit Reached';
                % end
                % switch polarity
                %     case -1
                %         isAtSecondPhase = isLimitReached == 1 || isLimitReached == -2;
                %     case 1
                %         isAtSecondPhase = isLimitReached == -1 || isLimitReached == 2;
                % end
                % if isAtSecondPhase
                %     waveformStatus = ['*' limitStatus '*'];
                % else
                    waveformStatus = limitStatus;
                % end
            elseif isAtMaxCurrent
                waveformStatus = 'Max Current';
            elseif (isAmplitudeRepeat || ~isPrecise) && ~isLimitReached && ~isPulsing
                waveformStatus = 'NOT Enough Precision';
            elseif isDataBad && ~isPulsing
                waveformStatus = 'Waveform NOT Normal';
            elseif isTooLong && ~isPulsing
                waveformStatus = 'Took Too Long';
            elseif isAtVoltageCompliance
                waveformStatus = 'Voltage Compliance';
            elseif ~isVoltageSafe
                waveformStatus = 'NOT Safe';
            else
                waveformStatus = '';
            end

            if isempty(waveformStatus)
                title_use = channelName;
            else
                title_use = sprintf( ...
                    '%s (%s)', ...
                    channelName,waveformStatus);
            end
            if isPTP
                title(ax,title_use,'FontSize',18.5);
            else
                title(ax,title_use);
            end
            drawnow;

            %% Check Waveform
            % startTime = tic;
            % fprintf('\t');
            if ~isWaveformNorm
                % Attempts
                if attemptNum > maxAttempts
                    % fprintf('Too many attempts to normalize %s...',channelName);
                    %             isGood = false;
                    isWaveformNorm_tf(scopeChannel_idx) = true;
                    % [endTime,unit] = getEndTime(startTime);
                    % fprintf('moving on (%.2f %s)\n',endTime,unit);
                else
                    attemptNum = attemptNum + 1;  % increment attempt number
                    % fprintf('%s not normalized...',channelName);
                    % fprintf('trying again.\n');
                end
                if hasCurrent
                    hasCurrent = false;
                end
            else
                % fprintf('%s normalized...',channelName);
                isWaveformNorm_tf(scopeChannel_idx) = true;
                % [endTime,unit] = getEndTime(startTime);
                % fprintf('moving on (%.2f %s)\n',endTime,unit);
            end
        end

        %% Store
        % polarization
        if ~isVoltageSpecial
            activeDriving_arr = potential_arr(driving_idx_arr);
        else
            activeDriving_arr = [drivingVoltage1_plot;drivingVoltage2_plot;drivingVoltage3_plot];
        end
        if isReturnFound
            return_arr = File.Data(groupNum).Capture(captureNum).(returnField_name);
            returnDriving_arr = return_arr(driving_idx_arr);
%             try
                drivingPotential_arr = [activeDriving_arr;returnDriving_arr];
%             catch
%                 if hasInterphaseDelay
%                     if hasDischargeDelay
%                         drivingPotential_arr = [NaN NaN NaN NaN];
%                     else
%                         drivingPotential_arr = [NaN NaN NaN];
%                     end
%                 else
%                     if hasDischargeDelay
%                         drivingPotential_arr = [NaN NaN NaN];
%                     else
%                         drivingPotential_arr = [NaN NaN];
%                     end
%                 end
%             end
        else
            drivingPotential_arr = activeDriving_arr;
        end
        if ~isVoltageSpecial || hasAltActive
            File.Data(groupNum).Capture(captureNum). ...
                DrivingPotential = drivingPotential_arr;
        end

        if hasCurrent && (isLimitReached || isBad)
            isWaveformNorm_tf(1:numOfScopeChannels) = true;
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
    %         if ~isempty(displayTable)
    %             disp(displayTable);
    %         end

    %% Store
    File.Data(groupNum).PotentialExcursion = excursion_arr;
    % if isBP || isTP
        File.Data(groupNum).ReturnExcursion = returnExcursion_arr;
    % end
    drivingVoltage_mat = [ ...
        drivingVoltage1 ...
        drivingVoltage2 ...
        drivingVoltage3];
    File.Data(groupNum).DrivingVoltage = drivingVoltage_mat;
    File.Data(groupNum).EffectiveCapacitance = effectiveCapacitance;
    File.Data(groupNum).AccessVoltage = [ ...
        accessVoltage1 accessVoltage2 ...
        accessVoltage3 accessVoltage4 ...
        accessVoltage5 accessVoltage6];
    File.Data(groupNum).AccessResistance = [ ...
        accessResistance1 accessResistance2 ...
        accessResistance3 accessResistance4 ...
        accessResistance5 accessResistance6];
    File.Data(groupNum).ChargingCapacitance = chargingCapacitance;
    if ~isTooLong
        File.Data(groupNum).Capture(captureNum).Status.Description = status;
    end
    File.Data(groupNum).Capture(captureNum).Status.Quit = isQuit;
    [endCapture,unit] = getEndTime(startCapture);
    fprintf('Capture Time: %.2f %s\n',endCapture,unit);

    varargout{1} = File;
    varargout{2} = buttonHandle;
    varargout{3} = isQuit;

end
% catch err
%     stopStimAllChannels(1);
%     File.Error.Status = err;
%     report = getReport(err);
%     File.Error.Report = report;
%     display(report);
%     isQuit = true;
% end

varargout{1} = File;
varargout{2} = buttonHandle;
varargout{3} = isQuit;

end
