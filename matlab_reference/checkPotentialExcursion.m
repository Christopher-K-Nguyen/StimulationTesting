function [isLimitReached,status,varargout] = checkPotentialExcursion(File,varargin)
%% Constants
% Tolerance
TOL = 0.02;

%% Variables
% Channels
channel_arr = [File.Data(:).ActiveChannel];
groupNum = length(channel_arr);

% Captures
capture_arr = [File.Data(groupNum).Capture(:).Index];
captureNum = capture_arr(end);

% Limits
isLimitReached_check = File.Data(groupNum).Capture(captureNum).Status.PotentialLimit;
if nargin>1 && contains2(varargin{1},{'ref'})
    electrode = 'ReferenceElectrode';
else
    electrode = 'CounterElectrode';
end
lowerPotential = File.Parameters.(electrode).LowerPotential;
upperPotential = File.Parameters.(electrode).UpperPotential;

% Pattern
polarity = File.Parameters.Polarity;
inerphaseDelay = File.Parameters.InterphaseDelay;
hasInterphaseDelay = inerphaseDelay > 0;
dischargeDelay = File.Parameters.DischargeDelay;
hasDischargeDelay = dischargeDelay > 0;

% Data
Capture = File.Data(groupNum).Capture(captureNum);
field_cell = fieldnames(Capture);
timeField_idx = find(containsi(field_cell,'Time'));
currentDensity_idx = find(containsi(field_cell,'CurrentDensity'));
dataFields_cell = field_cell(timeField_idx+1:currentDensity_idx-1);
numOfDataFields = length(dataFields_cell);
empty_tf = zeros(numOfDataFields,1);
for dataFieldNum = 1:numOfDataFields
    field = dataFields_cell{dataFieldNum};
    try
        data = File.Data(groupNum).Capture(captureNum).(field);
    catch
        data = [];
    end
    empty_tf(dataFieldNum) = isempty(data);
end
empty_idx = flip(find(empty_tf));
if ~isempty(empty_idx)
    dataFields_cell(empty_idx) = [];
end
isReturnField_tf = containsi(dataFields_cell,{'ret','count'});
isReturnFound = any(isReturnField_tf);

% Excursion
activeExcursion_arr = File.Data(groupNum).Capture(captureNum).PotentialExcursion;
isNan_tf = isnan(activeExcursion_arr);
activeExcursion_arr(isNan_tf) = [];
activeExcursion_arr_round = round(activeExcursion_arr,3);
if isReturnFound
    returnExcursion_arr = File.Data(groupNum).Capture(captureNum).ReturnExcursion;
    isNan_tf = isnan(returnExcursion_arr);
    returnExcursion_arr(isNan_tf) = [];
    returnExcursion_arr_round = round(returnExcursion_arr,3);
end
numOfExcursions = length(activeExcursion_arr_round);
% phaseNum_arr = 1:numOfExcursions;

%% Initialize
isLimitReached = 0;
status = '';
isExceeded = false;

%% Phase-by-phase excursion test (active then return)
% if ~any(isLimitReached_check)
    lowerLimit_min = lowerPotential - TOL;
    lowerLimit_max = lowerPotential + TOL;
    upperLimit_min = upperPotential - TOL;
    upperLimit_max = upperPotential + TOL;
    isActiveWithin_tf = isbetween2( ...
        activeExcursion_arr_round, ...
        lowerLimit_min,upperLimit_max);
    isActiveWithin = all(isActiveWithin_tf);
    if isReturnFound
        isReturnWithin_tf = isbetween2( ...
            returnExcursion_arr_round, ...
            lowerLimit_min,upperLimit_max);
        isReturnWithin = all(isReturnWithin_tf);
    else
        isReturnWithin = true;
    end
    if isActiveWithin && isReturnWithin
        for excursion_idx = 1:numOfExcursions
            phasePoint = excursion_idx / 10;

            %--- Active excursion first ---
            activeExcursion = activeExcursion_arr_round(excursion_idx);
            activeExcursion_round = round(activeExcursion,3);
            isActiveWithinLower = isbetween2( ...
                activeExcursion_round, ...
                lowerLimit_min,lowerLimit_max);
            isActiveWithinUpper = isbetween2( ...
                activeExcursion_round, ...
                upperLimit_min,upperLimit_max);
            if isActiveWithinLower || isActiveWithinUpper
                switch polarity
                    case -1
                        % cathodic active → too negative?
                        if isActiveWithinLower
                            isLimitReached = -(1 + phasePoint);
                            status = sprintf('Active cathodic limit [Phase %d] reached',excursion_idx);
                            break;
                            % anodic active → too positive?
                        elseif isActiveWithinUpper
                            isLimitReached = +(1 + phasePoint);
                            status = sprintf('Active anodic limit [Phase %d] reached',excursion_idx);
                            break;
                        end
                    case 1
                        if isActiveWithinUpper
                            isLimitReached = +(1 + phasePoint);
                            status = sprintf('Active anodic limit [Phase %d] reached',excursion_idx);
                            break;
                        elseif isActiveWithinLower
                            isLimitReached = -(1 + phasePoint);
                            status = sprintf('Active cathodic limit [Phase %d] reached',excursion_idx);
                            break;
                        end
                end
            end

            %--- Then return excursion ---
            if isReturnFound && ~any(isLimitReached)
                returnExcursion = returnExcursion_arr_round(excursion_idx);
                returnExcursion_round = round(returnExcursion,3);
                isReturnWithinLower = isbetween2( ...
                    returnExcursion_round, ...
                    lowerLimit_min,lowerLimit_max);
                isReturnWithinUpper = isbetween2( ...
                    returnExcursion_round, ...
                    upperLimit_min,upperLimit_max);
                if isReturnWithinLower || isReturnWithinUpper
                    switch polarity
                        case -1
                            if isReturnWithinUpper
                                isLimitReached = +(2 + phasePoint);
                                status = sprintf('Return anodic limit [Phase %d] reached',excursion_idx);
                                break;
                            elseif isReturnWithinLower
                                isLimitReached = -(2 + phasePoint);
                                status = sprintf('Return cathodic limit [Phase %d] reached',excursion_idx);
                                break;
                            end
                        case 1
                            if isReturnWithinUpper
                                isLimitReached = +(2 + phasePoint);
                                status = sprintf('Return anodic limit [Phase %d] reached',excursion_idx);
                                break;
                            elseif isReturnWithinLower
                                isLimitReached = -(2 + phasePoint);
                                status = sprintf('Return cathodic limit [Phase %d] reached',excursion_idx);
                                break;
                            end
                    end
                end
            end
        end

        %% Report
        if isLimitReached ~= 0
            fprintf('\t%s\n',status);
        else
            fprintf('\tLimits not reached\n');
        end
    else
        isExceeded = true;
        % display(lowerLimit_min);
        % display(upperLimit_max);
        if ~isActiveWithin && ~isReturnWithin
            fprintf('\tActive and return limit(s) exceeded\n');
            % display(activeExcursion_arr_round);
            % display(returnExcursion_arr_round);
        elseif ~isActiveWithin
            fprintf('\tActive limit(s) exceeded\n');
            % display(activeExcursion_arr_round);
        elseif ~isReturnWithin
            fprintf('\tReturn limit(s) exceeded\n');
            % display(returnExcursion_arr_round);
        end
    end
% end

varargout{1} = isExceeded;

end
