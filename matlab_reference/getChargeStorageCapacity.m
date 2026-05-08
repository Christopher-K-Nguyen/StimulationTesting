function varargout = getChargeStorageCapacity( ...
    potential_mat, ...
    current_mat, ...
    varargin)
%% Constants
MICRO_TO_N = 1e-6;
N_TO_CENTI = 1e2;
N_TO_MILLI = 1e3;

%% Variables
% Default
defaultScanRate = 50e-3;
defaultStepSize = 10e-3;
defaultArea = 2e3;
defaultSign = -1;
defaultType = 'eic';
defaultLimit = max(potential_mat);

%% Parsing
p = inputParser;
validMatrix = @(x) isnumeric(x) && ismatrix(x);
validScalarPosNum = @(x) isnumeric(x) && isscalar(x) && (x > 0);
validScalarNum = @(x) isnumeric(x) && isscalar(x);
validScalarSign = @(x) isnumeric(x) && isscalar(x);
validSWords = @(x) ischar(x) && isstring(x);

addRequired(p,'Potential',validMatrix);
addRequired(p,'Current',validMatrix);
addParameter(p,'ScanRate',defaultScanRate,validScalarPosNum);
addParameter(p,'StepSize',defaultStepSize,validScalarPosNum);
addParameter(p,'Area',defaultArea,validScalarPosNum);
addParameter(p,'Sign',defaultSign,validScalarSign);
addParameter(p,'Type',defaultType,validSWords);
addParameter(p,'Limit',defaultLimit,validScalarNum);

parse(p,potential_mat,current_mat,varargin{:});
potential_mat = p.Results.Potential;
current_mat = p.Results.Current;
scanRate = p.Results.ScanRate;
stepSize = p.Results.StepSize;
area = p.Results.Area;
polarity = p.Results.Sign;
type = p.Results.Type;
limit = p.Results.Limit;

% Correction
if ~isempty(limit)
    % cathodicLimit = min(potential_mat);
    % anodicLimit = max(potential_mat);
    switch polarity
        case -1
            range_tf = potential_mat <= limit;
        case 1
            range_tf = potential_mat >= limit;
    end
    potential_mat(~range_tf) = NaN;
    current_mat(~range_tf) = NaN;
end

% Size
[dataLength,cvChannelList_len] = size(current_mat);
csc_mat = zeros(1,cvChannelList_len);

% Geometric surface area
area_m2 = area * (MICRO_TO_N)^2;      % um2 to m2
area_cm2 = area_m2 * (N_TO_CENTI)^2;  % m2 to cm2
% Scanning
if scanRate > 1
    scanRate = scanRate * 1e-3;
end
scanPeriod = stepSize / scanRate;
idx_arr = 1:dataLength;
time_arr = (idx_arr - 1) * scanPeriod;

%% Type
if strcmpi(type,'EIC')
    typeID = 'EIC';
elseif strcmpi(type,'HMRI')
    typeID = 'HMRI';
elseif strcmpi(type,'inside')
    typeID = 'INSIDE';
elseif strcmpi(type,'time')
    typeID = 'FIXED';
end

%% Function
for channel_idx = 1:cvChannelList_len
    current_arr = current_mat(:,channel_idx);
    potential_arr = potential_mat(:,channel_idx);
    switch polarity
        case -1
            sign_tf = current_arr <= 0;
        case 1
            sign_tf = current_arr >= 0;
    end
    current_phase = current_arr;
    current_phase(~sign_tf) = NaN;
    % current_fix = current_arr(sign_tf);
    % current_mat(:,channel_idx) = current_fix;
    % potential_fix = potential_arr(sign_tf);
    potential_phase(~sign_tf) = NaN;
    switch typeID
        case {'EIC','HMRI'}
            chargePerArea = zeros(dataLength,1);
            for stepNum = 2:dataLength
                prevStep = stepNum - 1;
                switch typeID
                    case 'EIC'
                        charge = (current_phase(stepNum) + current_phase(prevStep)) * 0.5 * scanPeriod;
                        % charge = trapz(potential_arr, current_phase) / scanRate;
                    case 'HMRI'
                        charge = (abs(current_arr(stepNum)) + abs(current_arr(prevStep))) * 0.5 * scanPeriod;
                        % current_abs = abs(current_arr);
                        % charge = trapz(potential_arr, current_abs) / scanRate;
                end
                if isnan(charge)
                    charge = 0;
                end
                charge_mC = charge * N_TO_MILLI;
                chargePerArea(stepNum) = charge_mC / area_cm2;
            end
            totalCharge = sum(abs(chargePerArea));
            switch typeID
                case 'EIC'
                    csc_mat(channel_idx) = totalCharge;
                case 'HMRI'
                    csc_mat(channel_idx) = totalCharge * 0.5;
            end
        case {'INSIDE','FIXED'}
            % current_phase = abs(current_fix);
            % potential_phase = potential_fix;
            potential_phase_fix = unique(potential_phase);
            numOfPoints = length(potential_phase_fix);
            current_phase_fix = zeros(numOfPoints,1);
            for idx = 1:numOfPoints
                point = potential_phase_fix(idx);
                point_phase = find(potential_phase == point);
                point_phase_count = length(point_phase);
                if point_phase_count > 1
                    current_phase_point = current_phase(point_phase);
                    current_phase_diff = current_phase_point(2) - current_phase_point(1);
                    if isnan(current_phase_diff)
                        current_phase_diff = 0;
                    end
                    current_phase_fix(idx) = abs(current_phase_diff);
                else
                    current_phase_point = current_phase(point_phase);
                    current_phase_fix(idx) = current_phase_point;
                end
            end
        try
            charge_inside_point = trapz(potential_phase_fix,current_phase_fix) / scanRate;
            charge_inside_point_mC = charge_inside_point * N_TO_MILLI;
            csc_inside = abs(charge_inside_point_mC) / area_cm2;
            csc_mat(channel_idx) = csc_inside;
        catch
%             charge_inside_point = 0;
%             charge_inside_point_mC = 0;
%             csc_inside = 0;
            csc_mat(channel_idx) = 0;
        end
    end
end
varargout{1} = csc_mat;
varargout{2} = potential_mat;
switch polarity
    case -1
        sign_tf = current_mat < 0;
    case 1
        sign_tf = current_mat > 0;
end
current_mat(~sign_tf) = 0;
varargout{3} = current_mat;

end