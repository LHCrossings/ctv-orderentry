This README file describes the contents of the files included in the
2009-08-03 version of the proposed proposal for spot TV, spot cable 
and spot radio media.  This version is intended as a 1.0 schema that 
should be used in conjunction with the business rules document. 

NOTE: The routing information formerly in the transmission section of every
document was moved to a separate envelope wrapper document maintained
by the AAAA.
 
NOTE: The third number in the version represents the number of times the
AAAA and TVB schemas have changed while the proposal schema has remained
the same.  

README-0.3.0.5R.txt - This document.

aaaaMessageHeader-0.2.0.1.xsd - This file contains a chameleon 
schema that is the base of the template document used to create the 
proposal documents.  

TVBGeneralTypes-0.0.1.xsd -This file contains a TVB library of  generic and
non-media related types.  This file was previously maintained by the AAAA.

spotTV-2.4.1.0.xsd - This file contains a TVB library of TVB elements and types.  

spotTVCableProposal-0.3.0.5R.xsd - This file contains an updated version of the 
proposal schema for spot TV and spot cable media that now also supports 
spot radio media. The major change from the prior version, i.e., 0.3.0.5 and 0.3.0.5A, 
is the addition of a new RadioStation element as a choice for the Outlets element. 
This new version can be used for validating version 0.3.0.5 and version 0.3.0.5A 
proposal XML documents.

SampleSpotTVProposal-0.3.0.5R.xml - This file contains a sample spot TV 
proposal based on the spotTVCableProposal-0.3.0.5R.xsd schema.

SampleSpotCableProposal-0.3.0.5R.xml - This file contains a sample spot cable 
proposal  based on the spotTVCableProposal-0.3.0.5R.xsd schema.

SampleSpotRadioProposal-0.3.0.5R.xml - This is a new file.  It contains a sample spot radio 
proposal  based on the spotTVCableProposal-0.3.0.5R.xsd schema.