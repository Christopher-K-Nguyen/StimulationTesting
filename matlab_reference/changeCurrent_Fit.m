function [File,currentStim_new,currentChange_new] = changeCurrent_Fit(File,varargin)
%% Constants
CURRENT_COMPLIANCE = 1e3;
ATTEMPTS = 100;

% Max potential and lower potential limit difference
MAX_POTENTIAL_DIFF_THRESH = [ ...
    600e-3, ...
    500e-3, ...
    400e-3, ...
    300e-3, ...
    200e-3, ...
    100e-3, ...
    80e-3, ...
    60e-3, ...
    50e-3, ...
    20e-3, ...
    10e-3, ...
    5e-3, ...
    2e-3, ...
    1e-3];

% Current increment (uA)
CURRENT_INCREMENT = [ ...
    36, ...
    32, ...
    28, ...
    24, ...
    16, ...
    12, ...
    10, ...
    8, ...
    6, ...
    4, ...
    2, ...
    1, ...
    0.5, ...
    0.1];

% Current decrement (uA)
CURRENT_DECREMENT = -[...
    54, ...
    48, ...
    40, ...
    36, ...
    32, ...
    28, ...
    24, ...
    20, ...
    16, ...
    12, ...
    8, ...
    6, ...
    4, ...
    2];

NUM_OF_IDX = length(MAX_POTENTIAL_DIFF_THRESH);
FIT_CELL = {'poly1','poly2','poly3','exp1'};
NUM_OF_FIT = length(FIT_CELL);

%% Variable
% Environment
environment = File.Parameters.Environment;
isAnimal = contains2(environment,'Animal');

% Index
channel_arr = [File.Data(:).ActiveChannel];
groupNum = length(channel_arr);
channelNum = channel_arr(groupNum);
testChannel_arr = File.Parameters.Channels.Test;
channel_idx = find(testChannel_arr == channelNum,1);
capture_arr = [File.Data(groupNum).Capture(:).Index];
numOfCaptures = length(capture_arr);
captureNum = capture_arr(numOfCaptures);


% Configuration
configID = File.Parameters.Configuration.ID;
isMP = contains2(configID,'MP');
isPBP = contains2(configID,'PBP');
isBP = contains2(configID,'BP') && ~isPBP;
isPTP = contains2(configID,'PTP');
isTP = contains2(configID,'TP') && ~isPTP;
isPartial = isPBP || isPTP;
isCG = contains2(configID,'CG');

% Experiment
expType = File.Test.Experiment;
isTriphasic = contains2(expType,'TV');

% Pattern
phaseWidth1 = File.Parameters.PhaseWidth1;        % first phase pulse width
phaseWidth2 = File.Parameters.PhaseWidth2;        % second phase pulse width
if isTriphasic
    phaseWidth3 = File.Parameters.PhaseWidth3;        % third phase pulse width
end
phaseWidth_ratio = phaseWidth1 / phaseWidth2;
stimRate = File.Parameters.StimulationRate;
polarity = File.Parameters.Polarity;
isSymmetric = File.Parameters.Symmetry;
dischargeDelay = File.Parameters.DischargeDelay;
hasDischargeDelay = dischargeDelay > 0;
if isTriphasic
    if hasDischargeDelay
        numOfExcursions = 3;
    else
        numOfExcursions = 2;
    end
else
    if hasDischargeDelay
        numOfExcursions = 2;
    else
        numOfExcursions = 1;
    end
end

% if isBP || isTP
% numOfExcursion = numOfExcursion * 2;
% end

% Amplitude
currentChange_arr = vertcat(File.Data(groupNum).Capture(:).CurrentChange);
currentChange_prev = currentChange_arr(captureNum);
amplitude_arr = vertcat(File.Data(groupNum).Capture(:).Amplitude);
currentStim_arr = abs(amplitude_arr);
currentStim = currentStim_arr(captureNum);
currentStim = roundStim(currentStim);
currentStim_new = currentStim;
precision = 0.05;

% Capacitance
capacitance = File.Data(groupNum).Capture(captureNum).Capacitance;

% Driving Voltage
drivingVoltage_arr = File.Data(groupNum).Capture(captureNum).DrivingVoltage;

% Access Resistance
accessResistance_arr = File.Data(groupNum).Capture(captureNum).AccessResistance;

% Potential Excursion
if isempty(varargin)
    electrode = 'ReferenceElectrode';
else
    tag = varargin{1};
    if contains2(tag,{'ref'})
        electrode = 'ReferenceElectrode';
    elseif contains2(tag,{'count'})
        electrode = 'CounterElectrode';
    end
    
end
lowerPotential = File.Parameters.(electrode).LowerPotential;
upperPotential = File.Parameters.(electrode).UpperPotential;
refElectrode = File.Parameters.(electrode).Type;
isSilver = contains2(refElectrode,'Ag');
TOL = 0.02;

% Return Open Circuit Potential
counterElectrode = File.Parameters.CounterElectrode.Type;
try
    ocp = File.Parameters.CounterElectrode.OpenCircuitPotential;
    ocp_sign = sign(ocp);
catch
    ocp = 0;
    ocp_sign = 0;
end

%% Fields
Capture = File.Data(groupNum).Capture(captureNum);
field_cell = fieldnames(Capture);
timeField_idx = find(containsi(field_cell,'Time'));
currentDensity_idx = find(containsi(field_cell,'CurrentDensity'));
fieldsUsed_cell = field_cell(timeField_idx+1:currentDensity_idx-1);
numOfDataFields = length(fieldsUsed_cell);
activeField_tf = containsi(fieldsUsed_cell,{'act','work','pot'});
hasActiveField = any(activeField_tf);
diffField_tf = containsi(fieldsUsed_cell,{'diff'});
voltageField_tf = containsi(fieldsUsed_cell,{'volt'});
if hasActiveField
    specialField_idx = find(activeField_tf);
elseif any(diffField_tf)
    specialField_idx = find(diffField_tf);
elseif any(voltageField_tf)
    specialField_idx = find(voltageField_tf);
else
    specialField_idx = 1;
end
specialField = fieldsUsed_cell{specialField_idx};
empty_tf = zeros(numOfDataFields,1);
for dataFieldNum = 1:numOfDataFields
    field = fieldsUsed_cell{dataFieldNum};
    try
        data = File.Data(groupNum).Capture(captureNum).(field);
    catch
        data = [];
    end
    empty_tf(dataFieldNum) = isempty(data);
end
empty_idx = flip(find(empty_tf));
if ~isempty(empty_idx)
    fieldsUsed_cell(empty_idx) = [];
end
isCurrentField_tf = containsi(fieldsUsed_cell,'curr');
hasCurrent = any(isCurrentField_tf);
isVoltField_idx = find(~isCurrentField_tf);
isSpecialFound = contains2(fieldsUsed_cell,specialField);
isActiveField_tf = containsi(fieldsUsed_cell,{'act','work','pot'});
isActiveFound = any(isActiveField_tf);
isReturnField_tf = containsi(fieldsUsed_cell,{'ret','count'});
isReturnFound = any(isReturnField_tf);


% Change
testType = File.Test.ID;
targetInjection = File.Test.ChargeInjection;
surfaceArea = File.Data(groupNum).SurfaceArea;
if ~isempty(targetInjection)
    targetCharge = targetInjection * surfaceArea * 1e-2;
else
    targetCharge = File.Test.ChargePhase;
end
targetAmplitude = round(targetCharge / phaseWidth1 * 1e3,1);
isTargetFixed = isscalar(targetAmplitude);
isTargetMax = contains2(testType,'max');
stepSize = File.Test.StepSize;
hasStepSize = stepSize > 0;
% isAtFixedTarget = currentStim == targetAmplitude;
hasTarget = isTargetFixed || isTargetMax;
currentChange = 0;
earlyCaptureNum_check = 0;

%% Function
if hasTarget
    % Scale
    scale = 1;
    if ~hasStepSize
        if any(isbetween2(surfaceArea,1e3,2e3))
            scale = 1.5;
        elseif any(surfaceArea > 2e3)
            scale = 2.5;
        elseif any(surfaceArea >= 3e3)
            scale = 7.5;
        elseif any(surfaceArea >= 4e3)
            scale = 10;
        elseif any(surfaceArea >= 5e3)
            scale = 15;
        elseif any(isbetween2(surfaceArea,200,1e3,'open'))
            scale = 0.5;
        elseif any(isbetween2(surfaceArea,100,200))
            scale = 1;
        elseif any(surfaceArea < 100)
            scale = 0.5;
        end

        if ~isAnimal
            scale = scale * 1.5;
            factor = 1.5;
        else
            if phaseWidth1 <= 100
                scale = scale * 1.5^(200 / phaseWidth1);
                factor = 7.5;
                if stimRate < 1e4
                    %     scale = scale * log(1e4) / log(stimRate / 10);
                    % elseif stimRate < 1e4
                    scale = scale * log(stimRate) / log(5);
                    % else
                    %     scale = scale * 0.75;
                end
            else
                factor = 2;
                if ~isSymmetric
                    if isMP
                        scale = scale * 1.5;
                    else
                        if phaseWidth_ratio < 1
                            scale = scale * 1.2;
                        elseif phaseWidth_ratio > 1
                            scale = scale * 1.5;
                        end
                    end
                else
                    % scale = scale * 1.5;
                    % if ~isMP
                    %     scale = scale * 1.5;
                    % end
                end
            end
        end

        % Electrode Materials
        scale = scale * setElectrodeScale(File);

        earlyCaptureNum_check = 0;
        if surfaceArea < 2000
            if isAnimal
                earlyCaptureNum_check = 6;
            else
                %if ~isSymmetric
                if phaseWidth1 < 100
                    earlyCaptureNum_check = 3;
                else
                    if ~isMP
                        earlyCaptureNum_check = 5;
                    else
                        earlyCaptureNum_check = 6;
                    end
                end
            end
        end
        if earlyCaptureNum_check > 0
            switch captureNum
                case 1
                    scale = scale * 0.1;
                case 2
                    scale = scale * 0.2;
                case 3
                    scale = scale * 0.4;
                case 4
                    scale = scale * 0.5;
                case 5
                    scale = scale * 0.6;
                case 6
                    scale = scale * 0.8;
                case 7
                    scale = scale * 0.9;
            end
            captureNumforExp = earlyCaptureNum_check;
        else
            if isMP
                if captureNum > 3
                    captureNumforExp = 0.75;
                else
                    captureNumforExp = 1;
                end
                switch captureNum
                    % case 1
                    %     if any(abs(drivingVoltage_arr) > 3)
                    %         scale = scale * 0.05;
                    %     else
                    %         scale = scale * 0.1;
                    %     end
                    % case 2
                    %     scale = scale * 0.2;
                    % case 3
                    %     scale = scale * 0.4;
                    % case 4
                    %     scale = scale * 0.5;
                    % case 5
                    %     scale = scale * 0.6;
                    % case 6
                    %     scale = scale * 0.8;
                    % case 7
                    %     scale = scale * 0.9;
                    case 1
                        scale = scale * 0.125;
                    case 2
                        scale = scale * 0.25;
                    case 3
                        scale = scale * 0.375;
                    case 4
                        scale = scale * 0.5;
                    case 5
                        scale = scale * 0.75;
                        % case 6
                        %     scale = scale * 0.75;

                end
            else
                if captureNum > 4
                    captureNumforExp = 2;
                else
                    captureNumforExp = 1;
                end
                switch captureNum
                    case 1
                        scale = scale * 0.375;
                        case 2
                            scale = scale * 0.5;
                        case 3
                            scale = scale * 0.75;
                        % case 4
                        %     scale = scale * 0.75;
                end
            end
        end
        % if isMP
        %     sizeForFit = 4;
        % else
            sizeForFit = 4;
        % end


        if isAnimal
            if any(accessResistance_arr(1) < 2)
                scale = scale * 0.25;
            elseif any(accessResistance_arr(1) > 10)
                scale = scale * 0.5;
            end
        else
            if isMP
                if any(accessResistance_arr(1) > 6)
                    scale = scale * 0.1;
                elseif any(accessResistance_arr(1) > 3.5)
                    scale = scale * 0.5;
                elseif any(accessResistance_arr(1) < 0.5)
                    scale = scale * 0.5;
                end
            end
        end

        if captureNum < 3
            if any(abs(drivingVoltage_arr) > 1)
                scale = scale * 0.1;
            elseif any(abs(drivingVoltage_arr) > 0.5)
                scale = scale * 0.2;
            elseif any(abs(drivingVoltage_arr) > 0.2)
                scale = scale * 0.5;
            end
        else
            if any(abs(drivingVoltage_arr) > 4.5)
                scale = scale * 0.05;
            elseif any(abs(drivingVoltage_arr) > 2.5)
                scale = scale * 0.1;
            end
        end

        if any(capacitance)
            if capacitance < 10
                scale = scale * 0.01;
            elseif capacitance < 50
                scale = scale * 0.1;
            elseif capacitance < 100
                scale = scale * 0.5;
            else
                scale = scale * 0.1;
            end
        end

        if polarity * ocp_sign > 0 && abs(ocp) > 0.18
            scale = scale * 0.75;
        end
    end

    fprintf('\n');
    fprintf('Changing current amplitude...\n');
    startTime = tic;

    % Check
    activeExcursion_arr = vertcat(File.Data(groupNum).Capture(captureNum).PotentialExcursion);
    returnExcursion_arr  = [];
    if captureNum > 1
        activeExcursionList = vertcat(File.Data(groupNum).Capture(:).PotentialExcursion);
        if isReturnFound
            returnExcursion_arr = vertcat(File.Data(groupNum).Capture(captureNum).ReturnExcursion);
            returnExcursionList = vertcat(File.Data(groupNum).Capture(:).ReturnExcursion);
        end
    else
        activeExcursionList = File.Data(groupNum).Capture(1).PotentialExcursion;
        if isReturnFound
            returnExcursionList = File.Data(groupNum).Capture(1).ReturnExcursion;
        end
    end
    switch polarity
        case -1
            %         activeLower_arr = min(activeExcursionList,[],2);
            %         activeUpper_arr = max(sactiveExcursionList,[],2);
            %         % if isBP || isTP
            %             returnLower_arr = min(returnExcursionList,[],2);
            %             returnUpper_arr = max(returnExcursionList,[],2);
            %         % end
            limit1 = lowerPotential;
            limit2 = upperPotential;
        case 1
            %         activeLower_arr = activeExcursionList(:,2);
            %         activeUpper_arr = activeExcursionList(:,1);
            %         if isReturnFound
            %             returnLower_arr = returnExcursionList(:,1);
            %             returnUpper_arr = returnExcursionList(:,2);
            %         end
            limit1 = upperPotential;
            limit2 = lowerPotential;
    end

    activeLower_arr = min(activeExcursionList,[],2);
    activeUpper_arr = max(activeExcursionList,[],2);
    returnLower_arr = min(returnExcursionList,[],2);
    returnUpper_arr = max(returnExcursionList,[],2);
    % if isBP || isTP
    over_tf = ...
        (activeLower_arr < lowerPotential) | ...
        (activeUpper_arr > upperPotential) | ...
        (returnLower_arr < lowerPotential) | ...
        (returnUpper_arr > upperPotential);
    veryOver_tf = ...
        any(activeExcursion_arr < lowerPotential - 2*TOL) || ...
        any(activeExcursion_arr > upperPotential + 2*TOL) || ...
        any(returnExcursion_arr < lowerPotential - 2*TOL) || ...
        any(returnExcursion_arr > upperPotential + 2*TOL);
    under_tf = ...
        (activeLower_arr > lowerPotential) | ...
        (activeUpper_arr < upperPotential) | ...
        (returnLower_arr > lowerPotential) | ...
        (returnUpper_arr < upperPotential);
    % else
    %     over_tf = ...
    %         (activeLower_arr < lowerPotential) | ...
    %         (activeUpper_arr > upperPotential);
    %     under_tf = ...
    %         activeLower_arr > lowerPotential | ...
    %         activeUpper_arr < upperPotential;
    % end

    currentStimOver_arr = [];
    currentStimUnder_arr = [];
    if captureNum > 1
        currentStimOver_arr = currentStim_arr(over_tf);
        currentStimUnder_arr = currentStim_arr(under_tf);
    else
        if over_tf
            currentStimOver_arr = currentStim;
        elseif under_tf
            currentStimUnder_arr = currentStim;
        end
    end
    check_tf = ismember(currentStimUnder_arr,currentStimOver_arr);
    currentStimUnder_arr(check_tf) = [];
    currentStim_over = roundStim(min(currentStimOver_arr));
    currentStim_under = roundStim(max(currentStimUnder_arr));
    isVeryOver = any(veryOver_tf);
    hasCurrentStimUnder = ~isempty(currentStim_under);
    hasCurrentStimOver = ~isempty(currentStim_over);
    if hasCurrentStimUnder && hasCurrentStimOver
        isCurrentBoundsValid = currentStim_over > currentStim_under ...
            && abs(currentStim_over - currentStim_under) > precision;
        % if abs(currentStim_under - currentStim_over) < precision
            % display(currentStim_arr);
            % fprintf('\n');
            % display(activeLower_arr);
            % display(activeUpper_arr);
            % fprintf('\n');
            % display(currentStimOver_arr);
            % display(currentStimUnder_arr);
            % fprintf('\n');
            % display(currentStim_over);
            % display(currentStim_under);
            % error('Current amplitude bounds the same!');
        % end
    else
        isCurrentBoundsValid = false;
    end

    % Distance to limit
    active_lower = activeLower_arr(captureNum);
    active_upper = activeUpper_arr(captureNum);
    % fprintf('Voltage difference from potential limit: %.3f V\n',excursionDiff_mag);
    % lower excursion
    activeLower_diff = active_lower - lowerPotential;
    activeLower_diff_mag = abs(activeLower_diff);
    isActiveTooLow = active_lower < (lowerPotential - TOL);
    % upper excursion
    activeUpper_diff = upperPotential - active_upper;
    activeUpper_diff_mag = abs(activeUpper_diff);
    isActiveTooHigh = active_upper > (upperPotential + TOL);

    if isActiveTooLow || isActiveTooHigh
        active_diff_sign = -1;
        if isActiveTooLow && isActiveTooHigh
            active_diff_mag_arr = [activeLower_diff_mag activeUpper_diff_mag];
            active_diff_mag = max(active_diff_mag_arr);
            fprintf('\t\tDecrease current for lower and upper active potential excursions\n');
        elseif isActiveTooLow
            active_diff_mag = activeLower_diff_mag;
            fprintf('\t\tDecrease current for lower active potential excursion\n');
        elseif isActiveTooHigh
            active_diff_mag = activeUpper_diff_mag;
            fprintf('\t\tDecrease current for upper active potential excursion\n');
        end
    else
        active_diff_sign = 1;
        switch polarity
            case -1
                active_diff_mag = activeLower_diff_mag;
                fprintf('\t\tIncrease current for lower active potential excursion\n');
            case 1
                active_diff_mag = activeUpper_diff_mag;
                fprintf('\t\tIncrease current for upper active potential excursion\n');
        end
    end

    if isReturnFound
        return_lower = returnLower_arr(captureNum);
        return_upper = returnUpper_arr(captureNum);
        returnLower_diff = return_lower - lowerPotential;
        returnLower_diff_mag = abs(returnLower_diff);
        isReturnTooLow = return_lower < (lowerPotential - TOL);
        returnUpper_diff = upperPotential - return_upper;
        returnUpper_diff_mag = abs(returnUpper_diff);
        isReturnTooHigh = return_upper > (upperPotential + TOL);

        if isReturnTooLow || isReturnTooHigh
            return_diff_sign = -1;
            if isReturnTooLow && isReturnTooHigh
                return_diff_mag_arr = [returnLower_diff_mag returnUpper_diff_mag];
                return_diff_mag = max(return_diff_mag_arr);
                fprintf('\t\tDecrease current for lower and upper return potential excursions\n');
            elseif isReturnTooLow
                return_diff_mag = returnLower_diff_mag;
                fprintf('\t\tDecrease current for lower return potential excursion\n');
            elseif isReturnTooHigh
                return_diff_mag = returnUpper_diff_mag;
                fprintf('\t\tDecrease current for upper return potential excursion\n');
            end
        else
            if isMP
                return_diff_sign = 0;
            else
                return_diff_sign = 1;
                % For return, pick whichever one is closer to the limit
                if returnLower_diff_mag < returnUpper_diff_mag
                    return_diff_mag = returnLower_diff_mag;
                    fprintf('\t\tIncrease current for lower return potential excursion\n');
                else
                    return_diff_mag = returnUpper_diff_mag;
                    fprintf('\t\tIncrease current for upper return potential excursion\n');
                end
            end
        end

        if active_diff_sign < 0 || return_diff_sign < 0
            if active_diff_sign < 0 && return_diff_sign < 0
                % Both active and return exceed limits → decrease current
                excursion_diff_sign = -1;
                excursion_diff_mag_arr = [active_diff_mag return_diff_mag];
                excursion_diff_mag = max(excursion_diff_mag_arr);
                fprintf('\tDecrease current for active and return potential excursions\n');
            elseif active_diff_sign < 0
                excursion_diff_sign = -1;
                excursion_diff_mag = active_diff_mag;
                fprintf('\tDecrease current for active potential excursion\n');
            elseif return_diff_sign < 0
                excursion_diff_sign = -1;
                excursion_diff_mag = return_diff_mag;
                fprintf('\tDecrease current for return potential excursion\n');
            end
        else
            % Neither exceeds limit → increase current
            excursion_diff_sign = 1;
            if any(return_diff_sign)
                if active_diff_mag < return_diff_mag
                    excursion_diff_mag = active_diff_mag;
                    fprintf('\tIncrease current for active potential excursion\n');
                else
                    excursion_diff_mag = return_diff_mag;
                    fprintf('\tIncrease current for return potential excursion\n');
                end
            else
                excursion_diff_mag = active_diff_mag;
            end
        end
    else
        excursion_diff_sign = active_diff_sign;
        excursion_diff_mag = active_diff_mag;
    end

    if ~isSymmetric || ~isMP
        currentChangeSign_arr = sign([currentChange_arr;excursion_diff_sign]);
        if captureNumforExp > 0 && earlyCaptureNum_check > 0 && captureNum > captureNumforExp
            for capture_idx = 1:captureNum
                capture_arr = capture_idx:captureNum;
                num = length(capture_arr);
                currentChangeSign_check = currentChangeSign_arr(capture_arr);
                isSame = allSame(currentChangeSign_check);
                if isSame && num > captureNumforExp
                    scale = scale * (factor ^ (num / captureNumforExp));
                    break;
                end
            end
        end
    end
    currentInc = CURRENT_INCREMENT * scale;
    currentDec = CURRENT_DECREMENT * scale;

    % fprintf('Max Potential sign: %d\n',excursionDiff_sign);
    % excursionDiff_sign = sign(excursionDiff);
    switch excursion_diff_sign
        case 1
            fprintf('\tStimulation BELOW target: %0.3f V\n',excursion_diff_mag);
        case -1
            fprintf('\tStimulation ABOVE target: %0.3f V\n',excursion_diff_mag);
    end

    if hasStepSize
        fprintf('Changing current amplitude %.2f uA...',currentStim);
        stepSize_new = excursion_diff_mag * stepSize;
        isAmplitudeRepeat = true;
        while isAmplitudeRepeat
            currentStim_new = roundStim(currentStim_new + stepSize_new);
            if stepSize_new < 0.05
                break;
            else
                if captureNum > 1
                    isAmplitudeRepeat_tf = ismember(currentStim_arr,currentStim_new);
                    isAmplitudeRepeat_idx = find(isAmplitudeRepeat_tf);
                    isAmplitudeRepeat = length(isAmplitudeRepeat_idx) > 2;
                    % isAmplitudeRepeat = any(isAmplitudeRepeat_tf);
                else
                    isAmplitudeRepeat = false;
                end
                if isAmplitudeRepeat
                    stepSize_new = roundStim(stepSize * 0.5);
                end
            end
        end
        fprintf('+%.2f uA...',stepSize_new);
        % currentStim_new = currentStim + stepSize_new;   % increment current by step size
        currentChange_new = polarity * stepSize_new;

    else
        %     fprintf('Potential difference: %0.3f V\n',potfprintf('\t');entialExcursion_diff_mag);
        fprintf('\tChecking current amplitude...');
        checkTime = tic;
        for excursion_idx = 1:NUM_OF_IDX
            if excursion_diff_mag > MAX_POTENTIAL_DIFF_THRESH(excursion_idx)
                break;
            end
        end

        if excursion_diff_sign < 0
            currentChange_sign_char = '-';
            currentChange = currentDec(excursion_idx);
        else
            currentChange_sign_char = '+';
            currentChange = currentInc(excursion_idx);
        end
        % if over_tf(captureNum)  % <-- if potential limits were exceeded
        %     currentChange_sign_char = '-';
        %     currentChange = currentDec(idx);  % Decrease current
        % else
        %     currentChange_sign_char = '+';
        %     currentChange = currentInc(idx);  % Increase current
        % end
        currentChange = roundStim(currentChange);
        currentStim_new = currentStim + currentChange;

        %% Fitting
        currentStim_guess = [];
        if captureNum >= sizeForFit && ~isVeryOver
            fprintf('fitting...\n');
            % Define the fits to try
            File.Data(groupNum).Fitting = cell(numOfExcursions,2,2);
            fit_check = cell(numOfExcursions,2,2);
            limitDiff_arr = [];

            for excursion_idx = 1:numOfExcursions
                fprintf('\t\tPotential excursion %d...\n',excursion_idx);
                for electrode_idx = 1:2
                    switch electrode_idx
                        case 1
                            electrodeFit = 'Active';
                            excursion_arr = activeExcursionList;
                            lowerLimit = File.Parameters.(electrode).LowerPotential;
                            upperLimit = File.Parameters.(electrode).UpperPotential;
                        case 2
                            electrodeFit = 'Return';
                            excursion_arr = returnExcursionList;
                            % if isBP || isTP 
                                lowerLimit = File.Parameters.(electrode).LowerPotential;
                                upperLimit = File.Parameters.(electrode).UpperPotential;
                            % else
                            %     lowerLimit = File.Parameters.CounterElectrode.LowerPotential;
                            %     upperLimit = File.Parameters.CounterElectrode.UpperPotential;
                            % end
                    end
                    y_norm = excursion_arr(1:captureNum,excursion_idx);
                    excursion_val = y_norm(captureNum);

                    for limit_idx = 1:2
                        switch limit_idx
                            case 1
                                limitFit = 'lower';
                                % y_norm = min(excursion_arr,[],2);
                                limit = limit1;
                                limitDiff = abs(excursion_val - lowerLimit);
                            case 2
                                limitFit = 'upper';
                                % y_norm = max(excursion_arr,[],2);r
                                limit = limit2;
                                limitDiff = abs(excursion_val - upperLimit);
                        end

                        % Fit
                        y_data = y_norm(:);
                        fitting = [];
                        r2 = 0;
                        isR2Good = false;
                        fitType_used = '';

                        for fit_idx = 1:NUM_OF_FIT
                            fitType = FIT_CELL{fit_idx};
                            try
                                [tempFit,gof] = fit(currentStim_arr,y_data,fitType);
                                r2 = gof.rsquare;
                            catch
                                continue;
                            end

                            % Check if good fit
                            switch fitType
                                case 'poly1'
                                    isR2Good = r2 > 0.8;
                                otherwise
                                    isR2Good = r2 > 0.85;
                            end

                            if isR2Good
                                fitting = tempFit;
                                fitType_used = fitType;
                                break;
                            end
                        end

                        % Save fitting
                        File.Data(groupNum).Fitting{excursion_idx,electrode_idx,limit_idx} = fitting;
                        fit_check{excursion_idx,electrode_idx,limit_idx} = fitType_used;

                        % Solve if fitting successful
                        if isR2Good
                            fprintf('\t\t\t%s %s: %s (r2 = %.2f)...',electrodeFit,limitFit,fitType,r2);
                            coeffs = coeffvalues(fitting);

                            x_solve = [];
                            x_raw = [];
                            switch fitType_used
                                case 'poly1'
                                    slope = coeffs(1);
                                    intercept = coeffs(2);
                                    x_raw = (limit - intercept) / slope;
                                case 'poly2'
                                    coeff_poly2 = [coeffs(1) coeffs(2) coeffs(3)-limit];
                                    x_raw = roots(coeff_poly2);
                                case 'poly3'
                                    coeff_poly3 = [coeffs(1) coeffs(2) coeffs(3) coeffs(4)-limit];
                                    x_raw = roots(coeff_poly3);
                                case 'exp1'
                                    a = coeffs(1);
                                    b = coeffs(2);
                                    x_raw = log(limit/a)/b;
                            end

                            % Filter
                            real_tf = imag(x_raw) == 0;
                            pos_tf = x_raw >= 0;
                            max_tf = x_raw <= 1e3;
                            x_tf = real_tf & pos_tf & max_tf;
                            x_solve = x_raw(x_tf);

                            % x_len = length(x_solve);
                            % if x_len > 1
                            %     for idx = 1:x_len
                            %         x = x_raw(idx);
                            %         fprintf('%.2f',x);
                            %         if idx < x_len
                            %             fprintf(', ');
                            %         end
                            %     end
                            %     fprintf(' uA...');
                            % end

                            if ~isempty(x_solve)
                                x_guess = roundStim(min(x_solve));
                                currentStim_guess = [currentStim_guess;x_guess]; %#ok<*AGROW>
                                limitDiff_arr = [limitDiff_arr;limitDiff];
                                fprintf('%.2f uA\n',x_guess);
                            else
                                fprintf('NONE\n');
                            end
                        end
                    end % limit_idx
                end % electrode_idx
            end % excursion_idx
            if ~isempty(currentStim_guess)
                fprintf('\tGuessing...');
                % [~,best_idx] = min(limitDiff_arr);
                % currentStim_new = roundStim(currentStim_guess(best_idx));
                currentStim_new = roundStim(min(currentStim_guess));
                currentChange = currentStim_new - currentStim;
            else
                fprintf('\tChoosing...');
            end
            fprintf('%.2f uA...',currentStim_new);
        end

        if currentStim_new <= 0
            currentStim_new = precision;
            currentChange = currentStim_new - currentStim;
        end
        isCurrentGood = false;
        count = 0;
        startTime_check = tic;
        while ~isCurrentGood
            if captureNum > 1
                isAmplitudeRepeat_tf = ismember(currentStim_arr,currentStim_new);
                isAmplitudeRepeat_idx = find(isAmplitudeRepeat_tf);
                isAmplitudeRepeat = length(isAmplitudeRepeat_idx) > 2;
                % isAmplitudeRepeat = any(isAmplitudeRepeat_tf);
            else
                isAmplitudeRepeat = false;
            end

            if hasCurrentStimOver
                isCurrentStimOver = currentStim_new >= currentStim_over;
            else
                isCurrentStimOver = false;
            end
            if hasCurrentStimUnder
                isCurrentStimUnder = currentStim_new <= currentStim_under;
            else
                isCurrentStimUnder = false;
            end

            isCurrentGood = ~isCurrentStimOver && ~isCurrentStimUnder ...
                && ~isAmplitudeRepeat;
            if isCurrentGood
                currentStim_new = roundStim(currentStim) + currentChange;
                %                 currentChange = currentStim_new - currentStim;
                break;
            elseif count > ATTEMPTS || toc(startTime_check) > 1
                break;
            elseif isCurrentStimOver
                % if count > ATTEMPTS
                %     currentChange = currentChange - precision;
                % else
                currentChange = roundStim(currentChange * 0.9);
                % end
                currentStim_new = roundStim(currentStim + currentChange);
                currentChange = currentStim_new - currentStim;
            elseif isCurrentStimUnder
                % if count > ATTEMPTS
                %     currentChange = currentChange + precision;
                % else
                currentChange = roundStim(currentChange * 1.1);
                % end
                currentStim_new = roundStim(currentStim + currentChange);
                currentChange = currentStim_new - currentStim;
            end
        end
        currentStim_new = roundStim(currentStim_new);
        if currentStim_new >= CURRENT_COMPLIANCE
            currentStim_new = CURRENT_COMPLIANCE;
            currentChange = currentStim_new - currentStim;
        end
        if currentStim_new == 0
            currentStim_new = precision;
            currentChange = currentStim_new - currentStim;
        end
        if currentChange == currentChange_prev
            currentChange = roundStim(currentChange * 1.5);
            currentStim_new = currentStim + currentChange;
        end
        currentStim_new = roundStim(currentStim_new);

        if ~isempty(currentStim_guess)
            if captureNum <= sizeForFit + 1
                currentStim_guess = currentStim_guess * 0.9;
            end
            currentStim_new = roundStim(min(currentStim_guess));
            currentChange = currentStim_new - currentStim;
        end
        fprintf('%.2f uA...',currentStim_new);
        if captureNum > 1
            isAmplitudeRepeat_tf = ismember(currentStim_arr,currentStim_new);
            isAmplitudeRepeat_idx = find(isAmplitudeRepeat_tf);
            isAmplitudeRepeat = length(isAmplitudeRepeat_idx) > 2;
            % isAmplitudeRepeat = any(isAmplitudeRepeat_tf);
        else
            isAmplitudeRepeat = false;
        end
        if (isCurrentBoundsValid && isAmplitudeRepeat || ...
                isinf(currentStim_new) || isinf(currentChange) || ...
                ~any(currentChange)) || ...
                (captureNum > 10 && isCurrentBoundsValid)
            fprintf('%.2f uA (under) and %.2f uA (over)...', ...
                currentStim_under,currentStim_over);
            currentStim_outside = [currentStim_under currentStim_over];
            currentStimOutside_mean = mean(currentStim_outside);
            currentStim_new = roundStim(currentStimOutside_mean);
            if currentStim_new >= CURRENT_COMPLIANCE
                currentStim_new = CURRENT_COMPLIANCE;
            end
            if ~any(currentStim_new)
                currentStim_new = precision;
            end
            currentStim_new = roundStim(currentStim_new);
            fprintf('%.2f uA...',currentStim_new);
        end

        fprintf('checking...');
        currentChange_fix_sign = -1;
        while length(find(ismember(currentStim_arr,currentStim_new))) > 1
            currentChange_fix = currentChange_fix_sign * precision;
            currentStim_new = currentStim_new + currentChange_fix;
            currentStim_new = round(currentStim_new,2);
            if ~any(currentStim_new)
                switch currentChange_fix_sign
                    case -1
                        currentChange_fix_sign = 1;
                        currentStim_new = currentStim;
                    case 1
                        currentStim_new = precision;
                        break;
                end
            end
            fprintf('%.2f uA...',currentStim_new);
        end
        currentChange = currentStim_new - currentStim;
        [endTime,unit] = getEndTime(checkTime);
        fprintf('OK \t\t(%.2f %s)\n',endTime,unit);
        fprintf('\tChanging current amplitude %.2f uA...',currentStim);
        currentChange_mag = abs(currentChange);
        currentChange_new = currentChange;
        msg = sprintf('%s%.2f uA...',currentChange_sign_char,currentChange_mag);
        % if contains2(msg,'0.00')
        %     error('Current change is ZERO!')
        % end
        % if contains2(msg,'Inf')
        %     error('Current change is INFINITY!')
        % end
        fprintf(msg);
    end

    currentStim_new = abs(currentStim_new);
    [endTime,unit] = getEndTime(startTime);
    fprintf('%.2f uA\n',currentStim_new);
    fprintf('\tTime elapsed: %.2f %s\n',endTime,unit);
    % excursionTresh = MAX_POTENTIAL_DIFF_THRESH(idx);
    % fprintf('Voltage difference threshold: %.3f V',excursionTresh);
else
    currentStim_new = currentStim;
    currentChange_new = 0;
end

end
