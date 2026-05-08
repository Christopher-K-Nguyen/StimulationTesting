function currentDensity_arr = getCurrentDensity(current_arr,surfaceArea,varargin)
%% Variables
% Geometric surface area
surfaceArea_cm2 = surfaceArea * 1e-8;      % um2 to cm2

%% Units
numOfVar = length(varargin);
if numOfVar > 0
    unit_cell = cell(2,1);
    unit_arr = zeros(2,1);
    unit_cell{1} = varargin{1};
    unit_cell{2} = varargin{2};
    for unit_idx = 1:2
        unit = unit_cell{unit_idx};
        className = class(unit);
        switch className
            case 'double'
                unit_arr(unit_idx) = unit;
            case 'char'
                switch upper(unit_cell{unit_idx})
                    case {'A','BASE','B'}
                        unit_arr(unit_idx) = 1;
                    case {'MA','M','MILLI'}
                        unit_arr(unit_idx) = 1e-3;
                    case {'UA','U','MU'}
                        unit_arr(unit_idx) = 1e-6;
                    case {'NA','N','NANO'}
                        unit_arr(unit_idx) = 1e-9;
                end
        end
    end
    factor = unit_arr(1) / unit_arr(2);
else
    factor = 1e3;
end

%% Calculation
currentDensity_arr = current_arr * factor / surfaceArea_cm2;

end