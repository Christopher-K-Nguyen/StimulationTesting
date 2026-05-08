function [currentStim_new,currentChange_new] = changeCurrent(File)
%% Constants
CURRENT_COMPLIANCE = 1e3;
ATTEMPTS = 100;

% Max potential and lower potential limit difference
ACCEPT_DIFF = 0.02;
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
    48, ...
    36, ...
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
% if captureNum > 20
%     precision = 1.5;
% elseif captureNum > 10
%     precision = 1;
% else
    precision = 0.1;
% end

% Configuration
configID = File.Parameters.Configuration.ID;
isMP = contains2(configID,'MP');
isPBP = contains2(configID,'PBP');
isBP = contains2(configID,'BP') && ~isPBP;
isPTP = contains2(configID,'PTP');
isTP = contains2(configID,'TP') && ~isPTP;
isPartial = isPBP || isPTP;
isCG = contains2(configID,'CG');

% Pattern
phaseWidth1 = File.Parameters.PhaseWidth1;        % first phase pulse width
phaseWidth2 = File.Parameters.PhaseWidth2;        % second phase pulse width
phaseWidth_ratio = phaseWidth1 / phaseWidth2;
stimRate = File.Parameters.StimulationRate;
polarity = File.Parameters.Polarity;
isSymmetric = File.Parameters.Symmetry;

% Amplitude
currentChange_arr = [File.Data(groupNum).Capture(:).CurrentChange];
amplitude1_arr = [File.Data(groupNum).Capture(:).Amplitude];
amplitude1List = transpose(amplitude1_arr);
currentStimList = abs(amplitude1List);
currentStim = currentStimList(captureNum);
currentStim = roundStim(currentStim);
currentStim_new = currentStim;

% Capacitance
capacitance = File.Data(groupNum).Capture(captureNum).Capacitance;

% Access Resistance
accessResistance_arr = File.Data(groupNum).Capture(captureNum).AccessResistance;

% Potential Excursion
% potentialExcursion_arr = File.Data(groupNum).Capture(captureNum).PotentialExcursion;
lowerPotential = File.Parameters.ReferenceElectrode.LowerPotential;
upperPotential = File.Parameters.ReferenceElectrode.UpperPotential;

% Return Open Circuit Potential
counterElectrode = File.Parameters.CounterElectrode.Type;
try
    ocp = File.Parameters.CounterElectrode.OpenCircuitPotential;
    ocp_sign = sign(ocp);
catch
    ocp = 0;
    ocp_sign = 0;
end

% Change
testType = File.Test.ID;
targetInjection = File.Test.ChargeInjection;
if ~isempty(targetInjection)
    surfaceArea_arr = File.Parameters.SurfaceArea;
    if isscalar(surfaceArea_arr)
        surfaceArea = surfaceArea_arr;
    else
        surfaceArea = surfaceArea_arr(channel_idx);
    end
    targetCharge = targetInjection * surfaceArea * 1e-2;
else
    targetCharge = File.Test.ChargePhase;
end
targetAmplitude = round(targetCharge / phaseWidth1 * 1e3,1);
isTargetFixed = ~isinf(targetAmplitude);
isTargetMax = contains2(testType,'max');
stepSize = File.Test.StepSize;
hasStepSize = any(stepSize);
isAtFixedTarget = currentStim == targetAmplitude;
isTowardsTarget = (isTargetFixed && hasStepSize) || isTargetMax;
currentChange = 0;

% Scale
surfaceArea = File.Data(groupNum).SurfaceArea;
scale = 1;
if any(isbetween2(surfaceArea,1e3,2e3))
    scale = 1.5;
elseif any(surfaceArea > 2e3)
    scale = 2;
elseif any(isbetween2(surfaceArea,200,1e3,'open'))
    scale = 0.5;
elseif any(isbetween2(surfaceArea,50,200))
    scale = 0.2;
elseif any(surfaceArea < 50)
    scale = 0.1;
end

if ~isAnimal
    scale = scale * 1.5;
    factor = 1.5;
else
    if phaseWidth1 <= 100
        scale = scale * (200 / phaseWidth1);
        factor = 4;
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
            if ~isMP
                scale = scale * 1.5;
            end
        end
    
    %     if captureNum < 3 || captureNum > 10
    %         scale = scale * 0.5;
    %     end
    end
end

% if captureNum > 30
%     scale = scale * 5;
% elseif captureNum > 20
%     scale = scale * 4;
% elseif captureNum > 15
%     scale = scale * 3;
% elseif captureNum > 10
%     scale = scale * 2;
% end

if contains2(counterElectrode,{'SS','IR'}) && ~contains2(counterElectrode,{'PT','PTIR','IRO'})
    if phaseWidth1 <= 100
        scale = scale * 1.25;
    else
        scale = scale * 0.8;
    end
elseif contains2(counterElectrode,{'PT','PTIR','IRO','Au'})
    if phaseWidth1 <= 100
        scale = scale * 2.5;
    else
        scale = scale * 1.5;
    end
elseif contains2(counterElectrode,'W')
    if phaseWidth1 <= 100
        scale = scale * 0.1;
    else
        scale = scale * 0.2;
    end
elseif contains2(counterElectrode,{'TI'})
    if phaseWidth1 <= 100
        scale = scale * 1.25;
    else
        scale = scale * 0.8;
    end
end

if isAnimal
    earlyCaptureNum_check = 6;
else
    %if ~isSymmetric
    if phaseWidth1 < 100
        earlyCaptureNum_check = 3;
    else
        earlyCaptureNum_check = 5;
    end
end
switch earlyCaptureNum_check
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
        scale = scale * 0.75;
end

if isAnimal
    if any(accessResistance_arr < 2)
        scale = scale * 0.25;
    elseif any(accessResistance_arr > 10)
        scale = scale * 0.5;
    end
else
    if any(accessResistance_arr < 0.5) %|| accessResistance > 3
        scale = scale * 0.5;
    end
end

if any(capacitance)
    if capacitance > 100
        scale = scale * 0.2;
    else
        scale = scale * 0.6;
    end
end

if polarity * ocp_sign > 0
    scale = scale * 0.75;
end


%% Function
if isTowardsTarget && ~isAtFixedTarget
    fprintf('\n');
    fprintf('Changing current amplitude...\n');
    startTime = tic;

    % Check
    potentialExcursion_arr = File.Data(groupNum).Capture(1).PotentialExcursion;
    if captureNum > 1
        excursionBothList = horzcat(File.Data(groupNum).Capture(:).PotentialExcursion);
    else
        excursionBothList = potentialExcursion_arr;
    end
    switch polarity
        case -1
            excursionList_lower = excursionBothList(1,:);
            excursionList_upper = excursionBothList(2,:);
        case 1
            excursionList_lower = excursionBothList(2,:);
            excursionList_upper = excursionBothList(1,:);
    end
    excursionList_lower = transpose(excursionList_lower);
    excursionList_upper = transpose(excursionList_upper);
    % lowerPotential_min = lowerPotential - ACCEPT_DIFF;
    % lowerPotential_max = lowerPotential + ACCEPT_DIFF;
    % upperPotential_min = upperPotential - ACCEPT_DIFF;
    % upperPotential_max = upperPotential + ACCEPT_DIFF;
    over_tf = ...
        excursionList_lower < lowerPotential | ...
        excursionList_upper > upperPotential;
    under_tf = ...
        excursionList_lower > lowerPotential | ...
        excursionList_upper < upperPotential;
    currentStimOver_arr = [];
    currentStimUnder_arr = [];
    if captureNum > 1
        currentStimOver_arr = currentStimList(over_tf);
        currentStimUnder_arr = currentStimList(under_tf);
    else
        if over_tf
            currentStimOver_arr = currentStim;
        elseif under_tf
            currentStimUnder_arr = currentStim;
        end
    end
    check_tf = ismember(currentStimUnder_arr,currentStimOver_arr);
    currentStimUnder_arr(check_tf) = [];
    currentStim_over = min(currentStimOver_arr);
    currentStim_under = max(currentStimUnder_arr);
    hasCurrentStimUnder = ~isempty(currentStim_under);
    hasCurrentStimOver = ~isempty(currentStim_over);
    if hasCurrentStimUnder && hasCurrentStimOver
        if currentStim_under == currentStim_over
            display(currentStimList);
            fprintf('\n');
            display(excursionList_lower);
            display(excursionList_upper);
            fprintf('\n');
            display(currentStimOver_arr);
            display(currentStimUnder_arr);
            fprintf('\n');
            display(currentStim_over);
            display(currentStim_under);
            error('Current amplitude bounds the same!');
        end
    end

    % Distance to limit
    excursion_lower = excursionList_lower(captureNum);
    excursion_upper = excursionList_upper(captureNum);
    % fprintf('Voltage difference from potential limit: %.3f V\n',excursionDiff_mag);
    % lower excursion
    excursionLower_diff = excursion_lower - lowerPotential;
    excursionLower_diff_sign = sign(excursionLower_diff);
    excursionLower_diff_mag = abs(excursionLower_diff);
    % upper excursion
    excursionUpper_diff = upperPotential - excursion_upper;
    excursionUpper_diff_sign = sign(excursionUpper_diff);
    excursionUpper_diff_mag = abs(excursionUpper_diff);
    if excursionLower_diff_sign < 0 || excursionUpper_diff_sign < 0
        excursion_diff_sign = -1;
        if excursionLower_diff_sign < 0 && excursionUpper_diff_sign < 0
            excursion_diff_mag_arr = [excursionLower_diff_mag excursionUpper_diff_mag];
            excursion_diff_mag = max(excursion_diff_mag_arr);
        elseif excursionLower_diff_sign < 0
            excursion_diff_mag = excursionLower_diff_mag;
        elseif excursionUpper_diff_sign < 0
            excursion_diff_mag = excursionUpper_diff_mag;
        end
    else
        excursion_diff_sign = 1;
        switch polarity
            case -1
                excursion_diff_mag = excursionLower_diff_mag;
            case 1
                excursion_diff_mag = excursionUpper_diff_mag;
        end
    end
    if ~isSymmetric || ~isMP
        currentChangeSign_arr = sign([currentChange_arr,excursion_diff_sign]);
        capture_check = 5;
        if captureNum > capture_check
            for capture_idx = 1:captureNum
                capture_arr = capture_idx:captureNum;
                num = length(capture_arr);
                currentChangeSign_check = currentChangeSign_arr(capture_arr);
                isSame = allSame(currentChangeSign_check);
                if isSame && num > capture_check
                    scale = scale * (factor ^ (num / capture_check));
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
    if isTargetMax   % stimulating to max cathodal limit
        %     fprintf('Potential difference: %0.3f V\n',potfprintf('\t');entialExcursion_diff_mag);
        fprintf('\tChecking current amplitude...');
        checkTime = tic;
        for idx = 1:NUM_OF_IDX
            if excursion_diff_mag > MAX_POTENTIAL_DIFF_THRESH(idx)
                break;
            else
                continue;
            end
        end
        switch excursion_diff_sign
            case 1 % below target
                currentChange_sign_char = '+';
                currentChange = currentInc(idx);
            case -1 % above target
                currentChange_sign_char = '-';
                currentChange = currentDec(idx);
        end
        currentChange = roundStim(currentChange);
        currentStim_new = currentStim + currentChange;
        if currentStim_new <= 0
            currentStim_new = precision;
            currentChange = currentStim_new - currentStim;
        end
        isCurrentGood = false;
        count = 0;
        startTime_check = tic;
        while ~isCurrentGood
            if captureNum > 1
                isAmplitudeRepeat_tf = ismember(currentStimList,currentStim_new);
                isAmplitudeRepeat_idx = find(isAmplitudeRepeat_tf);
                isAmplitudeRepeat = length(isAmplitudeRepeat_idx) > 1;
                % isAmplitudeRepeat = any(ismember(currentStimList,currentStim_new));
            else
                isAmplitudeRepeat = 1;
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
        currentStim_new = roundStim(currentStim_new);
        fprintf('%.2f uA...',currentStim_new);
        if captureNum > 1
            isAmplitudeRepeat_tf = ismember(currentStimList,currentStim_new);
            isAmplitudeRepeat_idx = find(isAmplitudeRepeat_tf);
            isAmplitudeRepeat = length(isAmplitudeRepeat_idx) > 1;
        else
            isAmplitudeRepeat = false;
        end
        if hasCurrentStimOver && hasCurrentStimUnder && isAmplitudeRepeat || ...
                isinf(currentStim_new) || isinf(currentChange) || ...
                ~any(currentChange)
            fprintf('%.2f uA (under) and %.2f uA (over)...',currentStim_under,currentStim_over);
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
        currentChange_fix_sign = -1;
        while length(find(ismember(currentStimList,currentStim_new))) > 1
            currentChange_fix = currentChange_fix_sign * precision;
            currentStim_new = currentStim_new + currentChange_fix;
            currentStim_new = round(currentStim_new,2);
            if ~any(currentStim_new)
                if currentChange_fix_sign == -1
                    currentChange_fix_sign = 1;
                    currentStim_new = currentStim;
                else
                    currentStim_new = precision;
                    break;
                end
            end
            fprintf('%.2f uA...',currentStim_new);
        end
        currentChange = currentStim_new - currentStim;
        [endTime,unit] = getEndTime(checkTime);
        fprintf('OK (%.2f %s)\n',endTime,unit);
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

    else % stimulating to charge-per-phase target
        fprintf('Changing current amplitude %.2f uA...',currentStim);
        fprintf('+%.2f uA...',stepSize);
        currentStim_new = currentStim_new + stepSize;   % increment current by step size
        currentChange_new = polarity * stepSize;
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