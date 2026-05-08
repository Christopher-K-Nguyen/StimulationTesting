function File = setStatus_Tek(File,deviceNum,status)
%% Variables
% Object
oscilloscope = File.Oscilloscope(deviceNum).Object;      % oscilloscope

% Input
toOpen = contains2(status,'open');
toClose = contains2(status,'close');

% Scope Status
scopeStatus = oscilloscope.Status;
isClose = strcmpi(scopeStatus,'closed');
isOpen = strcmpi(scopeStatus,'open');

%% Function
if toOpen && isClose
    fprintf('Opening oscilloscope status...');
elseif toClose && isOpen
    fprintf('Closing oscilloscope status...');
end
startTime = tic;

if toOpen && isClose
    status_check = oscilloscope.Status;
    while contains(status,status_check)
        try
            fopen(oscilloscope);
        catch
            pause(0.5);
        end
    end
elseif toClose && isOpen
    status_check = oscilloscope.Status;
    while contains(status,status_check)
        try
            fopen(oscilloscope);
        catch
            pause(0.5);
        end
    end
end

if (toOpen && isClose) || (toClose && isOpen)
    [endTime,unit] = getEndTime(startTime);
    fprintf('OK (%.2f %s)\n',endTime,unit); 
end

% Return object
File.Oscilloscope(deviceNum).Object = oscilloscope;



end